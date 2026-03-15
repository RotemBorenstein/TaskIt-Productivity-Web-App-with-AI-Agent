from functools import lru_cache

from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langchain.text_splitter import RecursiveCharacterTextSplitter
from pgvector.django import CosineDistance

from main.models import RagChunk

EMBEDDINGS = OpenAIEmbeddings(model="text-embedding-3-small")
SPLITTER = RecursiveCharacterTextSplitter(
    chunk_size=500,
    chunk_overlap=100,
    separators=["\n\n", "\n", ". ", " ", ""],
)


class _RetrieverAdapter:
    """Compatibility adapter for existing `as_retriever(...).invoke(...)` usage."""

    def __init__(self, store, search_kwargs=None):
        self.store = store
        self.search_kwargs = search_kwargs or {}

    def invoke(self, query: str) -> list[Document]:
        k = int(self.search_kwargs.get("k", 5))
        filter_obj = self.search_kwargs.get("filter", {}) or {}
        user_id = filter_obj.get("user_id")
        return self.store.similarity_search(query=query, k=k, user_id=user_id)


class PgVectorStoreCompat:
    """Small vector-store surface to avoid changing agent/tool call sites."""

    def add_documents(self, docs: list[Document]) -> None:
        if not docs:
            return

        texts = [doc.page_content for doc in docs]
        vectors = EMBEDDINGS.embed_documents(texts)
        rows = []
        for doc, vector in zip(docs, vectors):
            meta = doc.metadata or {}
            rows.append(
                RagChunk(
                    user_id=meta.get("user_id"),
                    doc_type=meta.get("doc_type", "note"),
                    doc_key=meta.get("doc_key", ""),
                    chunk_index=meta.get("chunk_index", 0),
                    subject_id=meta.get("subject_id"),
                    note_id=meta.get("note_id"),
                    subject_title=meta.get("subject_title", ""),
                    note_title=meta.get("note_title", ""),
                    content=doc.page_content,
                    embedding=vector,
                )
            )
        RagChunk.objects.bulk_create(rows)

    def delete(self, where: dict | None = None) -> None:
        where = where or {}
        doc_key = where.get("doc_key")
        user_id = where.get("user_id")
        qs = RagChunk.objects.all()
        if doc_key:
            qs = qs.filter(doc_key=doc_key)
        if user_id is not None:
            qs = qs.filter(user_id=user_id)
        qs.delete()

    def as_retriever(self, search_kwargs=None):
        return _RetrieverAdapter(self, search_kwargs=search_kwargs)

    def similarity_search(self, query: str, k: int = 5, user_id: int | None = None) -> list[Document]:
        query_vector = EMBEDDINGS.embed_query(query)
        qs = RagChunk.objects.all()
        if user_id is not None:
            qs = qs.filter(user_id=user_id)

        matches = qs.annotate(
            distance=CosineDistance("embedding", query_vector)
        ).order_by("distance")[:k]

        return [
            Document(
                page_content=row.content,
                metadata={
                    "user_id": row.user_id,
                    "doc_type": row.doc_type,
                    "doc_key": row.doc_key,
                    "chunk_index": row.chunk_index,
                    "subject_id": row.subject_id,
                    "subject_title": row.subject_title,
                    "note_id": row.note_id,
                    "note_title": row.note_title,
                },
            )
            for row in matches
        ]


@lru_cache(maxsize=1)
def get_vectorstore():
    return PgVectorStoreCompat()

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
    vs.delete(where={"doc_key": f"note:{note.id}", "user_id": note.subject.user_id})
    docs = _note_to_documents(note)
    vs.add_documents(docs)

def delete_indexed_note(note_id, user_id):
    vs = get_vectorstore()
    vs.delete(where={"doc_key": f"note:{note_id}", "user_id": user_id})
