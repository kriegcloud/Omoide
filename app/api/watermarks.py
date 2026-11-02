"""Review-only previews, explicit asynchronous approval, and exact backup restore."""
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from uuid import uuid4
import base64

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from PIL import Image
from sqlmodel import Session, select

from app.api._media_filters import folder_filter
from app.database import get_session
from app.config import settings
from app.models import Media, WatermarkCandidate, WatermarkHandle
from app.services import watermarks as wm
from app.services.mutation_operations import begin_operation, lookup_operation
from app.tasks.common import create_and_run_task
from app.tasks.watermarks import run_detect_watermarks, run_mutation

router=APIRouter()


class Selection(BaseModel):
    ids: list[int] = Field(min_length=1,max_length=200)
    operation_id: str = Field(default_factory=lambda: str(uuid4()))


class MaskEdit(BaseModel):
    mask_png_base64: str = Field(max_length=16_000_000)


class HandleInput(BaseModel):
    display_text: str = Field(min_length=3,max_length=500)


def _candidate(session, candidate_id):
    candidate=session.get(WatermarkCandidate,candidate_id)
    if not candidate: raise HTTPException(404,'Watermark candidate not found')
    return candidate


@router.get('')
def list_candidates(session: Session=Depends(get_session), cursor: int=0,
                    limit: int=Query(50,ge=1,le=200), status: str | None=None, folder: str | None=None):
    stmt=select(WatermarkCandidate).join(Media,Media.id==WatermarkCandidate.media_id).where(
        WatermarkCandidate.id>cursor,Media.missing_since.is_(None),folder_filter(folder))
    if status: stmt=stmt.where(WatermarkCandidate.status==status)
    rows=session.exec(stmt.order_by(WatermarkCandidate.id).limit(limit+1)).all()
    return {'items': rows[:limit], 'next_cursor': rows[limit-1].id if len(rows)>limit else None}


@router.post('/detect',status_code=202)
def detect(background_tasks: BackgroundTasks, scope: str=Query('pending',pattern='^(pending|all)$'),
           folder: str | None=None,session: Session=Depends(get_session)):
    folder_filter(folder)
    return create_and_run_task(session,background_tasks,'detect_watermarks',
        lambda task_id:run_detect_watermarks(task_id,scope,folder),params={'scope':scope,'folder':folder})


@router.get('/operations/{operation_id}')
def operation_receipt(operation_id: str,session: Session=Depends(get_session)):
    return lookup_operation(session,operation_id)


def _admit(action,request,session,background_tasks,response):
    ids=list(dict.fromkeys(request.ids)); media_ids=[]
    for candidate_id in ids:
        candidate=_candidate(session,candidate_id)
        if action=='approve' and (candidate.status!='proposed' or not candidate.preview_path):
            raise HTTPException(409,'All candidates must have proposed previews')
        if action=='restore' and (not candidate.backup_path or candidate.status not in ('applied','approved','failed')):
            raise HTTPException(409,'All candidates must have an applied backup')
        if candidate.media_id in media_ids: raise HTTPException(400,'Only one candidate per media may be selected')
        media_ids.append(candidate.media_id)
    op=begin_operation(session,request.operation_id,{'action':action,'candidate_ids':ids},media_ids)
    background_tasks.add_task(run_mutation,op.id)
    response.headers['X-Mutation-Operation-ID']=op.id
    response.headers['X-Mutation-Operation-URL']=f'/api/watermarks/operations/{op.id}'
    return {'receipt': lookup_operation(session,op.id)}


@router.post('/approve',status_code=202)
def approve(request:Selection,background_tasks:BackgroundTasks,response:Response,session:Session=Depends(get_session)):
    return _admit('approve',request,session,background_tasks,response)


@router.post('/restore',status_code=202)
def restore(request:Selection,background_tasks:BackgroundTasks,response:Response,session:Session=Depends(get_session)):
    return _admit('restore',request,session,background_tasks,response)


@router.post('/reject')
def reject(request:Selection,session:Session=Depends(get_session)):
    rows=[_candidate(session,i) for i in set(request.ids)]
    if any(row.status!='proposed' for row in rows): raise HTTPException(409,'Only proposed candidates can be rejected')
    for row in rows:
        row.status='rejected'; row.decided_at=datetime.now(timezone.utc); session.add(row)
    session.commit()
    return {'rejected': [row.id for row in rows]}


@router.get('/handles')
def handles(session:Session=Depends(get_session)):
    return session.exec(select(WatermarkHandle).order_by(WatermarkHandle.id)).all()


@router.post('/handles',status_code=201)
def create_handle(request:HandleInput,session:Session=Depends(get_session)):
    normalized=wm.normalize_handle(request.display_text)
    if len(normalized)<3: raise HTTPException(400,'Handle must contain at least three letters or digits')
    existing=session.exec(select(WatermarkHandle).where(WatermarkHandle.text_normalized==normalized)).first()
    if existing: raise HTTPException(409,'Handle already exists')
    handle=WatermarkHandle(text_normalized=normalized,display_text=request.display_text)
    session.add(handle);session.commit();session.refresh(handle)
    return handle


@router.delete('/handles/{handle_id}',status_code=204)
def delete_handle(handle_id:int,session:Session=Depends(get_session)):
    handle=session.get(WatermarkHandle,handle_id)
    if not handle: raise HTTPException(404,'Handle not found')
    session.delete(handle);session.commit()


@router.post('/backups/purge')
def purge_backups(older_than_days:int=Query(...,ge=1),session:Session=Depends(get_session)):
    cutoff=datetime.now(timezone.utc)-timedelta(days=older_than_days)
    purged=[]
    with wm.FILE_LOCK:
        rows=session.exec(select(WatermarkCandidate).where(WatermarkCandidate.backup_path.is_not(None))).all()
        for candidate in rows:
            media=session.get(Media,candidate.media_id)
            path=Path(candidate.backup_path)
            if not media or path.parent!=Path(media.path).parent/'.beep-originals': continue
            # Age the approval rather than the original's preserved mtime.
            stamp=candidate.applied_at
            if stamp is None: continue
            if stamp.tzinfo is None: stamp=stamp.replace(tzinfo=timezone.utc)
            if stamp>=cutoff: continue
            if path.is_symlink(): raise HTTPException(409,'Refusing symlink backup')
            if path.exists():
                if candidate.original_sha256 and wm.digest(path)!=candidate.original_sha256:
                    raise HTTPException(409,'Backup bytes changed; purge refused')
                path.unlink()
            candidate.backup_path=None;session.add(candidate);purged.append(candidate.id)
        session.commit()
    return {'purged':purged}


@router.get('/handles/{handle_id}')
def get_handle(handle_id:int,session:Session=Depends(get_session)):
    handle=session.get(WatermarkHandle,handle_id)
    if not handle: raise HTTPException(404,'Handle not found')
    return handle


@router.patch('/handles/{handle_id}')
def update_handle(handle_id:int,request:HandleInput,session:Session=Depends(get_session)):
    handle=get_handle(handle_id,session)
    normalized=wm.normalize_handle(request.display_text)
    if len(normalized)<3: raise HTTPException(400,'Handle must contain at least three letters or digits')
    duplicate=session.exec(select(WatermarkHandle).where(WatermarkHandle.text_normalized==normalized,WatermarkHandle.id!=handle_id)).first()
    if duplicate: raise HTTPException(409,'Handle already exists')
    handle.display_text=request.display_text;handle.text_normalized=normalized
    session.add(handle);session.commit();session.refresh(handle)
    return handle


@router.get('/{candidate_id}')
def get_candidate(candidate_id:int,session:Session=Depends(get_session)):
    return _candidate(session,candidate_id)


@router.get('/{candidate_id}/preview')
def preview(candidate_id:int,session:Session=Depends(get_session)):
    candidate=_candidate(session,candidate_id)
    if not candidate.preview_path: raise HTTPException(404,'Preview not available')
    return FileResponse(settings.general.thumb_dir/candidate.preview_path,media_type='image/jpeg')


@router.get('/{candidate_id}/mask')
def mask(candidate_id:int,session:Session=Depends(get_session)):
    candidate=_candidate(session,candidate_id)
    if not candidate.mask_path: raise HTTPException(404,'Mask not available')
    return FileResponse(settings.general.thumb_dir/candidate.mask_path,media_type='image/png')


@router.patch('/{candidate_id}/mask')
def edit_mask(candidate_id:int,request:MaskEdit,session:Session=Depends(get_session)):
    with wm.FILE_LOCK:
        candidate=_candidate(session,candidate_id)
        if candidate.status!='proposed': raise HTTPException(409,'Only proposed masks may be edited')
        media=session.get(Media,candidate.media_id)
        try:
            data=base64.b64decode(request.mask_png_base64,validate=True)
            with Image.open(BytesIO(data)) as image:
                if image.format!='PNG': raise ValueError('Mask must be PNG')
                image.load(); edited=image.convert('L')
            return wm.preview_candidate(session,media,candidate,edited)
        except (ValueError,OSError) as exc: raise HTTPException(400,str(exc)) from exc
