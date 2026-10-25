"""Regression proofs for cancellation, conversion rollback and host admission."""
import json
import errno
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sqlmodel import Session, SQLModel, create_engine, select
from app.config import MediaDirectory, settings
from app.models import (DatasetExport, DatasetExportLayout, DatasetExportStatus,
                        Media, ProcessingTask, TrainingDataset, TrainingRun)
from app import conversion
from app.tasks import dataset_export, dataset_caption_generation as captions
from app.tasks import dataset_frame_mining as mining
from app.tasks import conversion as worker
from app.services import training_runs


class RuntimeTransactionSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.engine = create_engine('sqlite://')
        self.addCleanup(self.engine.dispose)
        SQLModel.metadata.create_all(self.engine)
        for module in (dataset_export, captions, mining, worker):
            self.enterContext(patch.object(module.db, 'engine', self.engine))
        self.enterContext(patch.object(settings.general, 'media_dirs', [MediaDirectory(path=self.root)]))
        with Session(self.engine) as session:
            dataset = TrainingDataset(name='Synthetic', slug='synthetic', trigger_word='x', class_token='person')
            session.add(dataset); session.commit(); self.dataset_id = dataset.id

    def task(self, task_type, status):
        with Session(self.engine) as session:
            task = ProcessingTask(task_type=task_type, status=status)
            session.add(task); session.commit(); return task.id

    def export(self):
        with Session(self.engine) as session:
            export = DatasetExport(dataset_id=self.dataset_id, layout=DatasetExportLayout.AI_TOOLKIT,
                                   status=DatasetExportStatus.COMPLETED, output_dir=str(self.root/'export'))
            session.add(export); session.commit(); return export.id

    def test_terminal_dataset_workers_never_restart_or_call_effects(self):
        for status in ('cancelled', 'interrupted', 'failed'):
            for kind in ('export', 'captions', 'mining'):
                with self.subTest(status=status, kind=kind):
                    task_id = self.task(kind, status)
                    with patch.object(dataset_export, 'build_export') as build, \
                         patch.object(captions, 'create_annotation_attempt') as annotate, \
                         patch.object(mining, 'dataset_videos', return_value=[]) as videos:
                        if kind == 'export': dataset_export.export_dataset(task_id, self.export())
                        elif kind == 'captions': captions.generate_dataset_captions(task_id, dataset_id=self.dataset_id, only_missing=True)
                        else: mining.mine_dataset_frames(task_id, dataset_id=self.dataset_id)
                    with Session(self.engine) as session:
                        self.assertEqual(session.get(ProcessingTask, task_id).status, status)
                    build.assert_not_called(); annotate.assert_not_called(); videos.assert_not_called()

    def test_export_interruption_during_builder_is_preserved(self):
        task_id = self.task('export_dataset', 'pending')
        export_id = self.export()
        def interrupt(session, *_):
            task = session.get(ProcessingTask, task_id)
            task.status = 'interrupted'; session.add(task); session.commit()
        with patch.object(dataset_export, 'build_export', side_effect=interrupt):
            dataset_export.export_dataset(task_id, export_id)
        with Session(self.engine) as session:
            self.assertEqual(session.get(ProcessingTask, task_id).status, 'interrupted')
            self.assertNotEqual(session.get(DatasetExport, export_id).status, 'completed')

    def test_caption_interruption_is_preserved_without_annotation_work(self):
        from app.models import DatasetItem
        task_id = self.task('dataset_caption_generation', 'pending')
        with Session(self.engine) as session:
            media = Media(path='/synthetic/no-read.jpg',filename='no-read.jpg',size=1)
            session.add(media);session.flush()
            session.add(DatasetItem(dataset_id=self.dataset_id,media_id=media.id));session.commit()
        def interrupt(session, *_):
            task=session.get(ProcessingTask,task_id)
            task.status='interrupted';session.add(task);session.commit()
            return True
        with patch.object(captions,'_has_approved_caption',side_effect=interrupt),patch.object(captions,'create_annotation_attempt') as annotate:
            captions.generate_dataset_captions(task_id,dataset_id=self.dataset_id,only_missing=True)
        annotate.assert_not_called()
        with Session(self.engine) as session:
            self.assertEqual(session.get(ProcessingTask,task_id).status,'interrupted')

    def test_mining_interruption_after_inference_blocks_frame_publication(self):
        task_id=self.task('dataset_frame_mining','pending')
        with Session(self.engine) as session:
            video=Media(path='/synthetic/no-read.mp4',filename='no-read.mp4',size=1,duration=1)
            session.add(video);session.commit();video_id=video.id
        def interrupt(session, *_args, **_kwargs):
            task=session.get(ProcessingTask,task_id)
            task.status='interrupted';session.add(task);session.commit()
            return [object()]
        with patch.object(mining,'dataset_videos',return_value=[]),patch.object(mining,'mine_candidates',side_effect=interrupt),patch.object(mining,'select_candidates',side_effect=lambda values,_:values),patch.object(mining,'_register_frame') as register:
            mining.mine_dataset_frames(task_id,dataset_id=self.dataset_id,video_media_ids=[video_id])
        register.assert_not_called()
        with Session(self.engine) as session:
            self.assertEqual(session.get(ProcessingTask,task_id).status,'interrupted')

    def test_replacement_commit_failure_restores_original_and_catalog_both_paths(self):
        for suffix in ('.avi', '.mp4'):
            for route in ('shared', 'single'):
                with self.subTest(suffix=suffix, route=route):
                    case_root = self.root/f'{route}-{suffix[1:]}'
                    case_root.mkdir()
                    source = case_root/f'video{suffix}'
                    source.write_bytes(b'original')
                    with Session(self.engine) as session:
                        media = Media(path=str(source),filename=source.name,size=8)
                        task = ProcessingTask(task_type='convert',status='pending')
                        session.add_all([media,task]);session.commit()
                        media_id, task_id = media.id, task.id
                        if route == 'shared':
                            def encode(_source,target,**kwargs): target.write_bytes(b'converted')
                            with patch.object(conversion,'convert_file',side_effect=encode), \
                                 patch.object(session,'commit',side_effect=RuntimeError('commit fault')):
                                with self.assertRaisesRegex(RuntimeError,'commit fault'):
                                    conversion.convert_media(session,media,'replace_original')
                            session.rollback()
                    if route == 'single':
                        proc=Mock();proc.poll.return_value=0;proc.returncode=0;proc.communicate.return_value=('','')
                        def launch(cmd,**kwargs): Path(cmd[-1]).write_bytes(b'converted');return proc
                        original_commit=Session.commit
                        commits=0
                        def fail_final(session):
                            nonlocal commits
                            commits+=1
                            if commits==2: raise RuntimeError('commit fault')
                            return original_commit(session)
                        with patch.object(worker,'popen_silent',side_effect=launch), \
                             patch.object(worker,'ensure_ffmpeg_available',return_value=Path('/usr/bin/ffmpeg')), \
                             patch.object(worker.ffmpeg,'probe',return_value={}), \
                             patch.object(worker,'get_ffmpeg_accel_config',return_value=SimpleNamespace(video_encoder='libx264',hwaccel_args=[])), \
                             patch.object(Session,'commit',new=fail_final):
                            worker._run_conversion(task_id,str(source),media_id)
                    self.assertTrue(source.exists())
                    self.assertEqual(source.read_bytes(),b'original')
                    with Session(self.engine) as session:
                        row=session.get(Media,media_id)
                        self.assertEqual(row.path,str(source));self.assertEqual(row.filename,source.name)
                    self.assertTrue(any(p.read_bytes()==b'converted' for p in case_root.iterdir() if p.is_file() and p!=source), 'retain encoded recovery output')

    def test_conversion_supports_filesystems_without_hardlinks(self):
        for suffix in ('.avi','.mp4'):
            with self.subTest(suffix=suffix),Session(self.engine) as session:
                source=self.root/f'no-links-{suffix[1:]}{suffix}';source.write_bytes(b'original')
                media=Media(path=str(source),filename=source.name,size=8)
                session.add(media);session.commit()
                def encode(_source,target,**kwargs): target.write_bytes(b'converted')
                with patch.object(conversion,'convert_file',side_effect=encode),patch.object(conversion.os,'link',side_effect=OSError(errno.EOPNOTSUPP,'no hardlinks')):
                    final=conversion.convert_media(session,media,'replace_original')
                self.assertEqual(final.read_bytes(),b'converted')
                self.assertTrue(Path(media.path).exists())
                self.assertEqual(list(self.root.glob('*.backup')),[])

    def test_no_hardlink_commit_failure_retains_original_and_encoded_recovery(self):
        source=self.root/'no-link-failure.mp4';source.write_bytes(b'original')
        encoded=self.root/'encoded-recovery.mp4';encoded.write_bytes(b'converted')
        with Session(self.engine) as session:
            media=Media(path=str(source),filename=source.name,size=8)
            session.add(media);session.commit()
            with patch.object(conversion.os,'link',side_effect=OSError(errno.EOPNOTSUPP,'no hardlinks')),patch.object(session,'commit',side_effect=RuntimeError('commit fault')):
                with self.assertRaises(conversion.ConversionError):
                    conversion.publish_replacement(session,media,encoded,source)
            self.assertEqual(source.read_bytes(),b'original')
            self.assertEqual(encoded.read_bytes(),b'converted')

    def test_publication_collision_never_overwrites_unrelated_destination(self):
        source=self.root/'original.avi';source.write_bytes(b'original')
        encoded=self.root/'encoded.mp4';encoded.write_bytes(b'converted')
        destination=self.root/'original.mp4';destination.write_bytes(b'unrelated')
        with Session(self.engine) as session:
            media=Media(path=str(source),filename=source.name,size=8)
            session.add(media);session.commit()
            with self.assertRaises(conversion.ConversionError):
                conversion.publish_replacement(session,media,encoded,destination)
            self.assertEqual(source.read_bytes(),b'original')
            self.assertEqual(destination.read_bytes(),b'unrelated')
            self.assertEqual(encoded.read_bytes(),b'converted')

    def test_conversion_cleanup_preserves_newer_source_identity(self):
        source=self.root/'concurrent.avi';source.write_bytes(b'original')
        encoded=self.root/'encoded.mp4';encoded.write_bytes(b'converted')
        with Session(self.engine) as session:
            media=Media(path=str(source),filename=source.name,size=8)
            session.add(media);session.commit()
            def committed_then_changed():
                session.commit()
                replacement=self.root/'new-import';replacement.write_bytes(b'new user source')
                replacement.replace(source)
            conversion.publish_replacement(session,media,encoded,source.with_suffix('.mp4'),commit=committed_then_changed)
            self.assertTrue(source.exists())
            self.assertEqual(source.read_bytes(),b'new user source')
            self.assertEqual(encoded.read_bytes(),b'converted')
            self.assertTrue(any(p.read_bytes()==b'original' for p in self.root.glob('*.backup')))

    def test_conversion_rollback_preserves_newer_edit_and_both_recovery_versions(self):
        source=self.root/'concurrent.mp4';source.write_bytes(b'original')
        encoded=self.root/'encoded.mp4';encoded.write_bytes(b'converted')
        with Session(self.engine) as session:
            media=Media(path=str(source),filename=source.name,size=8)
            session.add(media);session.commit()
            def edited_then_failed():
                replacement=self.root/'new-edit';replacement.write_bytes(b'new user edit')
                replacement.replace(source)
                raise RuntimeError('commit fault')
            with self.assertRaises(conversion.ConversionError):
                conversion.publish_replacement(session,media,encoded,source,commit=edited_then_failed)
            self.assertEqual(source.read_bytes(),b'new user edit')
            self.assertEqual(encoded.read_bytes(),b'converted')
            self.assertTrue(any(p.read_bytes()==b'original' for p in self.root.glob('*.backup')))

    def test_inplace_source_edits_preserve_original_and_encoded_recovery(self):
        for suffix in ('.avi','.mp4'):
            with self.subTest(suffix=suffix),Session(self.engine) as session:
                source=self.root/f'inplace-{suffix[1:]}{suffix}';source.write_bytes(b'original')
                encoded=self.root/f'encoded-{suffix[1:]}.mp4';encoded.write_bytes(b'converted')
                media=Media(path=str(source),filename=source.name,size=8)
                session.add(media);session.commit()
                def edit_inplace():
                    if suffix=='.avi': session.commit()
                    source.write_bytes(b'new user edit')
                    if suffix=='.mp4': raise RuntimeError('commit fault')
                if suffix=='.mp4':
                    with self.assertRaisesRegex(conversion.ConversionError,'identity conflict'):
                        conversion.publish_replacement(session,media,encoded,source,commit=edit_inplace)
                else:
                    conversion.publish_replacement(session,media,encoded,source.with_suffix('.mp4'),commit=edit_inplace)
                self.assertEqual(source.read_bytes(),b'new user edit')
                self.assertEqual(encoded.read_bytes(),b'converted')
                self.assertTrue(any(p.read_bytes()==b'original' for p in self.root.glob('*.backup')))

    def test_publication_temp_cleanup_failure_keeps_durable_success(self):
        source=self.root/'cleanup.avi';source.write_bytes(b'original')
        encoded=self.root/'cleanup-encoded.mp4';encoded.write_bytes(b'converted')
        destination=source.with_suffix('.mp4')
        with Session(self.engine) as session:
            media=Media(path=str(source),filename=source.name,size=8)
            session.add(media);session.commit()
            original_unlink=Path.unlink
            def fail_temp(path,*args,**kwargs):
                if path.name.startswith('.omoide-publish-'): raise OSError('cleanup fault')
                return original_unlink(path,*args,**kwargs)
            with patch.object(Path,'unlink',new=fail_temp):
                conversion.publish_replacement(session,media,encoded,destination)
            self.assertEqual(destination.read_bytes(),b'converted')
            self.assertEqual(media.path,str(destination))
            self.assertFalse(source.exists())

    def test_stat_fault_after_publication_restores_catalog_and_original(self):
        source=self.root/'stat-fault.avi';source.write_bytes(b'original')
        encoded=self.root/'stat-encoded.mp4';encoded.write_bytes(b'converted')
        destination=source.with_suffix('.mp4')
        with Session(self.engine) as session:
            media=Media(path=str(source),filename=source.name,size=8)
            session.add(media);session.commit()
            identity=conversion._file_identity
            failed=False
            def fail_once(path):
                nonlocal failed
                if path==destination and destination.exists() and not failed:
                    failed=True
                    raise OSError('stat fault')
                return identity(path)
            with patch.object(conversion,'_file_identity',side_effect=fail_once):
                with self.assertRaises(conversion.ConversionError):
                    conversion.publish_replacement(session,media,encoded,destination)
            self.assertTrue(failed)
            self.assertEqual(source.read_bytes(),b'original')
            self.assertEqual(media.path,str(source))
            self.assertFalse(destination.exists())
            self.assertEqual(encoded.read_bytes(),b'converted')

    def test_postcommit_destination_conflict_preserves_recovery_and_durable_status(self):
        for suffix in ('.avi', '.mp4'):
            for change in ('replace', 'inplace'):
                with self.subTest(suffix=suffix, change=change), Session(self.engine) as session:
                    case = self.root / f'destination-{suffix[1:]}-{change}'
                    case.mkdir()
                    source = case / ('source' + suffix)
                    source.write_bytes(b'original')
                    encoded = case / 'encoded.mp4'
                    encoded.write_bytes(b'converted')
                    destination = source.with_suffix('.mp4')
                    media = Media(path=str(source), filename=source.name, size=8)
                    task = ProcessingTask(task_type='convert', status='running')
                    session.add_all([media, task]); session.commit()
                    def committed_then_changed():
                        task.status = 'completed'; session.add(task); session.commit()
                        if change == 'replace':
                            newer = case / 'newer-output'
                            newer.write_bytes(b'newer user output')
                            newer.replace(destination)
                        else:
                            destination.write_bytes(b'newer user output')
                    with self.assertLogs(conversion.logger, level='WARNING') as messages:
                        result = conversion.publish_replacement(
                            session, media, encoded, destination, commit=committed_then_changed,
                        )
                    self.assertEqual(result, destination)
                    self.assertEqual(destination.read_bytes(), b'newer user output')
                    if suffix == '.avi':
                        self.assertEqual(source.read_bytes(), b'original')
                    self.assertEqual(encoded.read_bytes(), b'converted')
                    self.assertTrue(any(p.read_bytes() == b'original' for p in case.glob('*.backup')))
                    session.refresh(task); session.refresh(media)
                    self.assertEqual(task.status, 'completed')
                    self.assertEqual(media.path, str(destination))
                    self.assertTrue(any('identity conflict' in message.lower() for message in messages.output))

    def test_training_commit_failure_cannot_publish_request(self):
        with Session(self.engine) as session:
            export=session.get(DatasetExport,self.export());dataset=session.get(TrainingDataset,self.dataset_id)
            with patch.object(training_runs,'_run_config',return_value=({'synthetic':True},'synthetic')), \
                 patch.object(session,'commit',side_effect=RuntimeError('commit fault')):
                with self.assertRaisesRegex(RuntimeError,'commit fault'):
                    training_runs.create_run(session,dataset,export,{})
            session.rollback()
            self.assertEqual(session.exec(select(TrainingRun)).all(),[])
            self.assertEqual(list(self.root.rglob('REQUESTED')),[])

    def test_training_marker_is_atomic_and_observes_committed_run(self):
        with Session(self.engine) as session:
            export=session.get(DatasetExport,self.export());dataset=session.get(TrainingDataset,self.dataset_id)
            original_replace=Path.replace
            observed=[]
            def replace(path,target):
                if Path(target).name=='REQUESTED':
                    with Session(self.engine) as observer:
                        rows=observer.exec(select(TrainingRun)).all()
                        self.assertEqual(len(rows),1)
                        self.assertEqual(json.loads(path.read_text())['run_id'],rows[0].id)
                    self.assertNotEqual(path.name,'REQUESTED');observed.append(True)
                return original_replace(path,target)
            with patch.object(training_runs,'_run_config',return_value=({'synthetic':True},'synthetic')),patch.object(Path,'replace',new=replace):
                run=training_runs.create_run(session,dataset,export,{})
            self.assertEqual(observed,[True]);self.assertTrue((Path(run.run_dir)/'REQUESTED').exists())

    def test_training_marker_failure_is_durable_failed_run_without_request(self):
        with Session(self.engine) as session:
            export=session.get(DatasetExport,self.export());dataset=session.get(TrainingDataset,self.dataset_id)
            with patch.object(training_runs,'_run_config',return_value=({'synthetic':True},'synthetic')), \
                 patch.object(Path,'replace',side_effect=OSError('publish fault')):
                with self.assertRaisesRegex(OSError,'publish fault'):
                    training_runs.create_run(session,dataset,export,{})
            session.rollback()
            rows=session.exec(select(TrainingRun)).all()
            self.assertEqual(len(rows),1);self.assertEqual(rows[0].status,'failed')
            self.assertEqual(list(self.root.rglob('REQUESTED')),[])


if __name__=='__main__': unittest.main()
