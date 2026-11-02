"""Resumable proposal scans and checkpointed, non-replayed file mutations."""
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image
from sqlmodel import Session, select

import app.database as db
from app.config import settings
from app.concurrency import heavy_writer
from app.models import Media, ProcessingTask, WatermarkCandidate, WatermarkHandle, MutationOperation
from app.services import watermarks as wm
from app.services.mutation_operations import Operation
from app.tasks.common import _start_task, _finish_task


def run_detect_watermarks(task_id, scope='pending', folder=None):
    from app.api._media_filters import folder_filter
    from app.services.vision_client import get_vision_client
    with Session(db.engine) as session:
        task=session.get(ProcessingTask,task_id)
        if not task or not _start_task(session,task): return
        params=task.params or {}; scope=params.get('scope',scope); folder=params.get('folder',folder)
        cursor=params.get('cursor',0)
        def cancelled():
            session.refresh(task)
            return task.status != 'running'
        with heavy_writer(name='detect_watermarks',cancelled=cancelled) as acquired:
            if not acquired: return
            while not cancelled():
                query=select(Media).where(Media.id>cursor,Media.duration.is_(None),Media.missing_since.is_(None),folder_filter(folder))
                if scope=='pending': query=query.where(Media.id.not_in(select(WatermarkCandidate.media_id)))
                media=session.exec(query.order_by(Media.id).limit(1)).first()
                if not media: break
                handles=session.exec(select(WatermarkHandle)).all()
                with Image.open(media.path) as source:
                    source.load(); image=source.convert('RGB'); image.info.clear()
                result=get_vision_client().detect_text([image])[0]
                regions=wm.select_regions(result['regions'],image.size,handles)
                candidate=WatermarkCandidate(media_id=media.id,regions=regions,
                    detector_version=wm.DETECTOR_VERSION,model_version=wm.MODEL_VERSION,
                    original_sha256=wm.digest(media.path),status='proposed' if regions else 'rejected')
                session.add(candidate); session.commit(); session.refresh(candidate)
                if regions:
                    try: wm.preview_candidate(session,media,candidate)
                    except Exception as exc:
                        candidate.status='failed'; candidate.error=f'{type(exc).__name__}: {exc}'[:1000]
                        session.add(candidate); session.commit()
                cursor=media.id; task.processed+=1
                task.params={**params,'scope':scope,'folder':folder,'cursor':cursor}
                session.add(task); session.commit()
        _finish_task(session,task,'completed')


def run_mutation(operation_id):
    with Session(db.engine) as session:
        row=session.get(MutationOperation,operation_id)
        if not row or row.receipt.get('state') != 'pending': return
        operation=Operation(session,row); action=row.snapshot['action']
        with heavy_writer(name='watermarks-'+action) as acquired:
            if not acquired: return
            session.refresh(row)
            if row.receipt.get('state') != 'pending': return
            for candidate_id in row.snapshot['candidate_ids']:
                candidate=session.get(WatermarkCandidate,candidate_id)
                if not candidate: continue
                media=session.get(Media,candidate.media_id)
                if not media: continue
                operation.start_member(media.id)
                try:
                    with operation.member(media.id):
                        with wm.FILE_LOCK:
                            if action=='approve':
                                if candidate.status!='proposed': raise ValueError('Candidate is not proposed')
                                with Image.open(media.path) as source: image=source.convert('RGB'); image.info.clear()
                                with Image.open(settings.general.thumb_dir/candidate.mask_path) as mask: edited_mask=wm.validate_mask(mask,image.size)
                                from app.services.vision_client import get_vision_client
                                cleaned=wm.hard_composite(image,get_vision_client().inpaint(image,edited_mask),edited_mask)
                                def checkpoint(backup,before,after):
                                    candidate.backup_path=str(backup); candidate.original_sha256=before
                                    candidate.cleaned_sha256=after; candidate.status='approved'
                                    candidate.decided_at=datetime.now(timezone.utc)
                                    session.add(candidate); session.commit()
                                wm.apply_image(media.path,cleaned,candidate.original_sha256,checkpoint)
                                candidate.applied_at=datetime.now(timezone.utc); candidate.status='applied'
                                # Persist filesystem outcome before derivative work, which can fail independently.
                                session.add(candidate); session.commit()
                                for region in candidate.regions:
                                    norm=wm.normalize_handle(region['text'])
                                    handle=session.exec(select(WatermarkHandle).where(WatermarkHandle.text_normalized==norm)).first()
                                    if not handle:
                                        handle=WatermarkHandle(text_normalized=norm,display_text=region['text'],source_candidate_id=candidate.id)
                                    handle.match_count+=1; session.add(handle)
                                session.commit()
                            else:
                                if candidate.status not in ('applied','failed','approved') or not candidate.backup_path:
                                    raise ValueError('Candidate has no applied backup')
                                wm.restore_image(media.path,candidate.backup_path,candidate.cleaned_sha256,candidate.original_sha256)
                                candidate.status='restored'; session.add(candidate); session.commit()
                            with Image.open(settings.general.thumb_dir/candidate.mask_path) as mask:
                                wm.refresh_media(session,media,mask.copy())
                            candidate.error=None; session.add(candidate)
                            operation.applied(media.id,commit=False); session.commit()
                except Exception as exc:
                    session.rollback(); session.refresh(candidate)
                    candidate.error=f'{type(exc).__name__}: {exc}'[:1000]
                    # Applied/restored remains factual even when refresh failed; receipt stays unknown.
                    if candidate.status not in ('applied','restored','approved'): candidate.status='failed'
                    session.add(candidate); session.commit()
            operation.finish({},200)
