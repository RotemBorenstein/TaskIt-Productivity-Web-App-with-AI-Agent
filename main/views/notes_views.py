from __future__ import annotations
import logging
from typing import Optional, List
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from ninja import NinjaAPI, Schema
from datetime import datetime
from ..models import Subject, Note
from ..agent.rag_utils import delete_indexed_note
from ..tasks import queue_note_index

api = NinjaAPI(title= "TaskIt notes api")
logger = logging.getLogger(__name__)


def _queue_note_indexing(note: Note) -> None:
    """Queue note indexing after commit without blocking the CRUD response."""
    queued_updated_at = note.updated_at.isoformat()

    def _enqueue() -> None:
        try:
            queue_note_index.delay(note.id, queued_updated_at)
            logger.info(
                "Queued note indexing: note_id=%s user_id=%s updated_at=%s",
                note.id,
                note.subject.user_id,
                queued_updated_at,
            )
        except Exception:
            logger.exception("Failed to queue note indexing: note_id=%s user_id=%s", note.id, note.subject.user_id)

    transaction.on_commit(_enqueue)


def _delete_indexed_note_safely(note_id: int, user_id: int) -> None:
    """Keep note deletion available even if vector cleanup fails."""
    try:
        delete_indexed_note(note_id, user_id)
    except Exception:
        logger.exception("Failed to delete indexed note %s for user %s", note_id, user_id)


#Schemas
class SubjectIn(Schema):
    title: str
    color: Optional[str] = None
class SubjectOut(Schema):
    id: int
    title: str
    color: str
    created_at: datetime
    updated_at: datetime

class NoteIn(Schema):
    subject_id: int
    title: str
    content: str = ""
    tags: str = ""
    pinned: bool = False

class NoteSummaryOut(Schema):
    id: int
    title: str
    pinned: bool
    updated_at: datetime

class NoteOut(NoteIn):
    id: int
    created_at: datetime
    updated_at: datetime

class NoteUpdate(Schema):
    subject_id: Optional[int] = None
    title: Optional[str] = None
    content: Optional[str] = None
    tags: Optional[str] = None
    pinned: Optional[bool] = None



@login_required
def notes_page(request):
    return render(request, "main/notes.html")

@login_required
@api.get("/subjects/", response=list[SubjectOut])
def list_subjects(request):
    subjects = Subject.objects.filter(user=request.user).order_by("title")
    return subjects

@login_required
@api.post("/subjects/", response=SubjectOut)
def create_subject(request, data: SubjectIn):
    subject = Subject.objects.create(
        user=request.user,
        title=data.title,
        color=data.color or ""
    )
    return subject

@login_required
@api.delete("/subjects/{subject_id}")
def delete_subject(request, subject_id: int):
    subject = get_object_or_404(Subject, user=request.user, id=subject_id)
    subject.delete()

@login_required
@api.patch("/subjects/{subject_id}", response=SubjectOut)
def update_subject(request, subject_id: int, data: SubjectIn):
    subject = get_object_or_404(Subject, user=request.user, id=subject_id)
    for field, value in data.dict(exclude_unset=True).items():
        setattr(subject, field, value)
    subject.save()
    return subject


@login_required
@api.get("/notes/", response=List[NoteSummaryOut])
def list_notes(request, subject_id: Optional[int] = None, q: Optional[str] = None,
    pinned: Optional[bool] = None,):
    try:
        notes = Note.objects.filter(subject__user=request.user)
        if subject_id:
            notes = notes.filter(subject_id=subject_id)
        if q:
            notes = notes.filter(Q(title__icontains=q) | Q(content__icontains=q))
        if pinned is not None:
            notes = notes.filter(pinned=pinned)

        return notes.only("id", "title", "pinned", "updated_at").order_by("-updated_at")
    except Exception:
        logger.exception("Failed to list notes for user %s", request.user.id)
        raise

@login_required
@api.get("/notes/{note_id}", response=NoteOut)
def get_note(request, note_id: int):
    try:
        return get_object_or_404(
            Note.objects.select_related("subject"),
            id=note_id,
            subject__user=request.user,
        )
    except Http404:
        raise
    except Exception:
        logger.exception("Failed to load note detail: note_id=%s user_id=%s", note_id, request.user.id)
        raise

@login_required
@api.post("/notes/", response=NoteOut)
def create_note(request, data: NoteIn):
    with transaction.atomic():
        subject = get_object_or_404(Subject, id=data.subject_id, user=request.user)
        note = Note.objects.create(
            subject=subject,
            title=data.title,
            content=data.content,
            pinned=data.pinned,
            tags=data.tags or "",
        )
        _queue_note_indexing(note)
    return note

@login_required
@api.patch("/notes/{note_id}", response=NoteOut)
def update_note(request, note_id: int, data: NoteUpdate):
    with transaction.atomic():
        note = get_object_or_404(Note, id=note_id, subject__user=request.user)
        # Only update fields provided in payload
        for field, value in data.dict(exclude_unset=True).items():
            if field == "subject_id":
                note.subject = get_object_or_404(Subject, id=value, user=request.user)
            else:
                setattr(note, field, value)
        note.save()
        _queue_note_indexing(note)
    return note

@login_required
@api.delete("/notes/{note_id}", response=dict)
def delete_note(request, note_id: int):
    note = get_object_or_404(Note, id=note_id, subject__user=request.user)
    note.delete()
    _delete_indexed_note_safely(note_id, request.user.id)
    return {"ok": True}


@login_required
@api.post("/notes/{note_id}/pin", response=NoteOut)
def pin_note(request, note_id: int):
    note = get_object_or_404(Note, id=note_id, subject__user=request.user)
    note.pinned = True
    note.save(update_fields=["pinned", "updated_at"])
    return note

@login_required
@api.post("/notes/{note_id}/unpin", response=NoteOut)
def unpin_note(request, note_id: int):
    note = get_object_or_404(Note, id=note_id, subject__user=request.user)
    note.pinned = False
    note.save(update_fields=["pinned", "updated_at"])
    return note

