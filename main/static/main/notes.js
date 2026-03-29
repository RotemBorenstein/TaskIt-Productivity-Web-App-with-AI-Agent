function getCSRFToken() {
  const cookieValue = document.cookie
    .split("; ")
    .find(row => row.startsWith("csrftoken="))
    ?.split("=")[1];
  return cookieValue || "";
}


const notesList = document.getElementById("notes-list");
const viewer = document.getElementById("viewer");
const viewerTitle = document.getElementById("viewer-title");
const viewerContent = document.getElementById("viewer-content");
const editor = document.getElementById("editor");

//subjects ------------------------------------------------
const subjectsList = document.getElementById("subjects-list");
let currentSubjectId = null;

// ---------- SUBJECTS WITH INLINE EDIT / DELETE ----------
async function loadSubjects() {
  const res = await fetch("/api/subjects/");
  if (!res.ok) return;
  const subjects = await res.json();

  subjectsList.innerHTML = "";
  subjects.forEach(sub => {
    const li = document.createElement("li");
    li.className = "subject";
    li.dataset.id = sub.id;

    li.innerHTML = `
      <div class="subject-main" style="display:flex;align-items:center;gap:8px;flex:1;">
        <span class="dot dot-${sub.color || "blue"}"></span>
        <span class="subject-title">${sub.title}</span>
      </div>
      <div class="subject-actions">
        <button class="edit-subject" title="Edit">
          <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z"/><path d="m15 5 4 4"/></svg>
        </button>
        <button class="delete-subject" title="Delete">
          <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/><line x1="10" x2="10" y1="11" y2="17"/><line x1="14" x2="14" y1="11" y2="17"/></svg>
        </button>
      </div>
    `;

    li.querySelector(".subject-main").addEventListener("click", e => {
      e.stopPropagation();
      selectSubject(sub.id);
    });

    li.querySelector(".edit-subject").addEventListener("click", e => {
      e.stopPropagation();
      startEditSubject(li, sub);
    });

    li.querySelector(".delete-subject").addEventListener("click", async e => {
      e.stopPropagation();
      if (!confirm(`Delete subject "${sub.title}" and all its notes?`)) return;
      const delRes = await fetch(`/api/subjects/${sub.id}`, {
        method: "DELETE",
        headers: { "X-CSRFToken": getCSRFToken() },
      });
      if (delRes.ok) loadSubjects();
    });

    subjectsList.appendChild(li);
  });

  if (subjects.length > 0) selectSubject(subjects[0].id);
}

function startEditSubject(li, sub) {
  const titleSpan = li.querySelector(".subject-title");
  const actions = li.querySelector(".subject-actions");
  const input = document.createElement("input");
  input.type = "text";
  input.value = sub.title;
  input.className = "subject-edit-input";

  li.querySelector(".subject-main").replaceChild(input, titleSpan);
  actions.style.display = "none";
  input.focus();

  input.addEventListener("keydown", async e => {
    if (e.key === "Enter") await updateSubjectTitle(sub.id, input.value.trim());
    else if (e.key === "Escape") cancelEditSubject(li, input, sub.title);
  });

  input.addEventListener("blur", async () => {
    await updateSubjectTitle(sub.id, input.value.trim());
  });
}

async function updateSubjectTitle(id, newTitle) {
  if (!newTitle) return loadSubjects();
  const res = await fetch(`/api/subjects/${id}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": getCSRFToken(),
    },
    body: JSON.stringify({ title: newTitle }),
  });
  if (res.ok) loadSubjects();
}

function cancelEditSubject(li, input, oldTitle) {
  const titleSpan = document.createElement("span");
  titleSpan.className = "subject-title";
  titleSpan.textContent = oldTitle;
  li.querySelector(".subject-main").replaceChild(titleSpan, input);
  li.querySelector(".subject-actions").style.display = "";
}

// notes --------------------------------------------------
let notesCache = [];
let currentNote = null;
let currentNoteId = null;
let activeNoteId = null;
let noteRequestToken = 0;

function noteSummaryFromDetail(note) {
  return {
    id: note.id,
    title: note.title,
    pinned: note.pinned,
    updated_at: note.updated_at,
  };
}

function updateCachedSummary(note) {
  const summary = noteSummaryFromDetail(note);
  const index = notesCache.findIndex(n => n.id == note.id);
  if (index === -1) {
    notesCache.unshift(summary);
    return;
  }
  notesCache[index] = summary;
}

function setActiveNote(id) {
  document.querySelectorAll(".note").forEach(n => n.classList.remove("active"));
  const el = notesList.querySelector(`[data-id="${id}"]`);
  if (el) el.classList.add("active");
}

function setCurrentNote(note) {
  currentNote = note;
  currentNoteId = note.id;
  activeNoteId = note.id;
  setActiveNote(note.id);
  viewerTitle.textContent = note.title;
  viewerContent.textContent = note.content;
  exitEditMode();
  showViewer();
}

function clearCurrentNote() {
  currentNote = null;
  currentNoteId = null;
  viewerTitle.textContent = "";
  viewerContent.textContent = "";
  exitEditMode();
}

function clearSelectedNote() {
  activeNoteId = null;
  clearCurrentNote();
}

async function selectSubject(subjectId) {
  currentSubjectId = subjectId;
  subjectsList.querySelectorAll(".subject").forEach(s => s.classList.remove("active"));
  const activeEl = subjectsList.querySelector(`[data-id="${subjectId}"]`);
  if (activeEl) activeEl.classList.add("active");

  clearSelectedNote();
  hideViewer();

  const res = await fetch(`/api/notes/?subject_id=${subjectId}`);
  if (!res.ok) return;
  const notes = await res.json();
  notesCache = notes;
  renderNotes(notes);
  window.currentSubjectId = subjectId;
  return true;
}

function renderNotes(notes) {
  notesList.innerHTML = "";
  notes.forEach(n => {
    const li = document.createElement("li");
    li.className = "note";
    li.dataset.id = n.id;
    li.innerHTML = `
      <div class="note-title">${n.pinned ? "<span class='pin'>&#9733;</span>" : ""} ${n.title}</div>
      <div class="note-sub">Updated ${new Date(n.updated_at).toLocaleDateString()}</div>
    `;
    li.addEventListener("click", () => openNote(n.id));
    notesList.appendChild(li);
  });

  if (activeNoteId) setActiveNote(activeNoteId);
}

function showViewer() {
  viewer.classList.remove("hidden");
  setTimeout(() => viewer.classList.add("show"), 10);
}
function hideViewer() {
  viewer.classList.remove("show");
  setTimeout(() => viewer.classList.add("hidden"), 400);
}

async function openNote(id) {
  activeNoteId = id;
  clearCurrentNote();
  setActiveNote(id);
  viewerTitle.textContent = "Loading...";
  showViewer();

  const requestToken = ++noteRequestToken;
  const res = await fetch(`/api/notes/${id}`);
  if (!res.ok) {
    if (requestToken === noteRequestToken && activeNoteId == id) {
      clearSelectedNote();
      hideViewer();
      document.querySelectorAll(".note").forEach(n => n.classList.remove("active"));
    }
    alert("Error loading note");
    return;
  }

  const note = await res.json();
  if (requestToken !== noteRequestToken || activeNoteId != id) return;

  updateCachedSummary(note);
  renderNotes(notesCache);
  setCurrentNote(note);
}

document.getElementById("edit-btn").addEventListener("click", () => {
  if (!currentNote) return;
  enterEditMode(currentNote);
});

document.getElementById("cancel-btn").addEventListener("click", () => {
  exitEditMode();
});

document.getElementById("save-btn").addEventListener("click", async () => {
  if (!currentNoteId) return;
  const saveTargetId = currentNoteId;
  const saveSubjectId = currentSubjectId;
  const title = document.getElementById("edit-title").value;
  const content = document.getElementById("edit-text").value;
  const res = await fetch(`/api/notes/${saveTargetId}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": getCSRFToken()
    },
    body: JSON.stringify({ title, content })
  });
  if (!res.ok) return alert("Error saving note");
  const updated = await res.json();
  if (currentSubjectId != saveSubjectId) return;
  updateCachedSummary(updated);
  renderNotes(notesCache);
  if (activeNoteId != saveTargetId) return;
  setCurrentNote(updated);
});

document.querySelector(".new-note-btn").addEventListener("click", async () => {
  if (!currentSubjectId) {
    alert("Select a subject first");
    return;
  }
  const createSubjectId = currentSubjectId;

  const res = await fetch("/api/notes/", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": getCSRFToken(),
    },
    body: JSON.stringify({
      subject_id: Number(currentSubjectId),
      title: "New Note",
      content: "",
    }),
  });

  if (!res.ok) {
    const msg = await res.text();
    alert("Error creating note:\n" + msg);
    return;
  }

  const newNote = await res.json();
  if (currentSubjectId != createSubjectId) return;
  notesCache.unshift(noteSummaryFromDetail(newNote));
  renderNotes(notesCache);
  setCurrentNote(newNote);
  enterEditMode(newNote);
});


// Add subject --------------------------------------------------
const addSubjectBtn = document.getElementById("add-subject");

addSubjectBtn.addEventListener("click", () => {
  if (subjectsList.querySelector(".new-subject-input")) return;

  const li = document.createElement("li");
  li.className = "subject new-subject-input";
  li.innerHTML = `
  <div class="subject-main" style="display:flex;align-items:center;gap:8px;flex:1;">
    <span class="dot dot-blue"></span>
    <input type="text" class="subject-input" placeholder="New subject..." autofocus>
  </div>
`;

  subjectsList.prepend(li);
  const input = li.querySelector("input");
  input.focus();

  const cancel = () => li.remove();

  input.addEventListener("keydown", async e => {
    if (e.key === "Enter") {
      const title = input.value.trim();
      if (!title) return cancel();
      const res = await fetch("/api/subjects/", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCSRFToken(),
        },
        body: JSON.stringify({ title }),
      });
      if (!res.ok) {
        alert("Error creating subject");
        return cancel();
      }
      await loadSubjects();
    } else if (e.key === "Escape") {
      cancel();
    }
  });

  input.addEventListener("blur", () => {
    setTimeout(cancel, 120);
  });
});

document.getElementById("close-btn").addEventListener("click", () => {
  clearSelectedNote();
  hideViewer();
  document.querySelectorAll(".note").forEach(n => n.classList.remove("active"));
});

document.getElementById("delete-note-btn").addEventListener("click", async () => {
  if (!currentNoteId) return;
  if (!confirm("Delete this note?")) return;
  const deleteTargetId = currentNoteId;
  const deleteSubjectId = currentSubjectId;

  const res = await fetch(`/api/notes/${deleteTargetId}`, {
    method: "DELETE",
    headers: { "X-CSRFToken": getCSRFToken() },
  });

  if (!res.ok) return alert("Error deleting note");

  notesCache = notesCache.filter(n => n.id != deleteTargetId);
  if (activeNoteId == deleteTargetId && currentSubjectId == deleteSubjectId) {
    clearSelectedNote();
    hideViewer();
  }
  renderNotes(notesCache);
});


function enterEditMode(note) {
  viewerContent.style.display = "none";
  document.getElementById("viewer-title").style.display = "none";
  editor.classList.remove("hidden");
  document.getElementById("edit-title").value = note.title;
  document.getElementById("edit-text").value = note.content;
}

function exitEditMode() {
  viewerContent.style.display = "block";
  document.getElementById("viewer-title").style.display = "block";
  editor.classList.add("hidden");
}

window.selectSubject = selectSubject;
window.openNote = openNote;
window.currentSubjectId = currentSubjectId;
window.loadSubjects = loadSubjects;


window.addEventListener("DOMContentLoaded", () => {
  loadSubjects();
});
