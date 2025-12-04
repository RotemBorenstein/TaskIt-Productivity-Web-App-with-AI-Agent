from langchain_openai import OpenAIEmbeddings
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain.text_splitter import RecursiveCharacterTextSplitter
from django.conf import settings
from main.models import Note, Subject, Task, Event
from functools import lru_cache

EMBEDDINGS = OpenAIEmbeddings(model="text-embedding-3-large")
SPLITTER = RecursiveCharacterTextSplitter(
    chunk_size=500,
    chunk_overlap=100,
    separators=["\n\n", "\n", ". ", " ", ""],
)
@lru_cache(maxsize=1)
def get_vectorstore():
    persist_dir = settings.BASE_DIR / "rag_index"
    return Chroma(
        collection_name="taskit_rag",
        embedding_function=EMBEDDINGS,
        persist_directory=str(persist_dir),
    )

def _note_to_documents(note):
    subject = note.subject
    header = (
        f"Subject: {subject.title}\n"
        f"Note title: {note.title}\n"
        f"Created: {note.created_at}\n\n"
    )
    full_text = header + (note.content or "")

    if len(full_text) < 800:
        chunks = [full_text]
    else:
        chunks = [c.page_content for c in SPLITTER.split_text(full_text)]

    docs = []
    for idx, chunk in enumerate(chunks):
        docs.append(
            Document(
                page_content=chunk,
                metadata={
                    "user_id": note.subject.user_id,
                    "doc_type": "note",
                    "doc_key": f"note:{note.id}",
                    "chunk_index": idx,
                    "subject_id": subject.id,
                    "subject_title": subject.title,
                    "note_id": note.id,
                    "note_title": note.title,
                },
            )
        )
    return docs


def index_note(note):
    vs = get_vectorstore()
    vs.delete(where={"doc_key": f"note:{note.id}"})
    docs = _note_to_documents(note)
    vs.add_documents(docs)

def delete_indexed_note(note_id, user_id):
    vs = get_vectorstore()
    vs.delete(where={"doc_key": f"note:{note_id}"})
