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
        <button class="edit-subject" title="Edit">✎</button>
        <button class="delete-subject" title="Delete">🗑</button>
      </div>
    `;

    // click to select subject
    li.querySelector(".subject-main").addEventListener("click", e => {
      e.stopPropagation();
      selectSubject(sub.id);
    });

    // edit
    li.querySelector(".edit-subject").addEventListener("click", e => {
      e.stopPropagation();
      startEditSubject(li, sub);
    });

    // delete
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

async function selectSubject(subjectId) {
  currentSubjectId = subjectId;
  subjectsList.querySelectorAll(".subject").forEach(s => s.classList.remove("active"));
  const activeEl = subjectsList.querySelector(`[data-id="${subjectId}"]`);
  if (activeEl) activeEl.classList.add("active");

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
      <div class="note-title">${n.pinned ? "<span class='pin'>★</span>" : ""} ${n.title}</div>
      <div class="note-sub">Updated ${new Date(n.updated_at).toLocaleDateString()}</div>
    `;
    li.addEventListener("click", () => openNote(n.id));
    notesList.appendChild(li);
  });
}

function showViewer() {
  viewer.classList.remove("hidden");
  setTimeout(() => viewer.classList.add("show"), 10);
}
function hideViewer() {
  viewer.classList.remove("show");
  setTimeout(() => viewer.classList.add("hidden"), 400);
}

function openNote(id) {
  const note = notesCache.find(n => n.id == id);
  if (!note) return;

  document.querySelectorAll(".note").forEach(n => n.classList.remove("active"));
  const el = notesList.querySelector(`[data-id="${id}"]`);
  if (el) el.classList.add("active");

  viewerTitle.textContent = note.title;
  viewerContent.textContent = note.content;
  exitEditMode();
  showViewer();
}

document.getElementById("edit-btn").addEventListener("click", () => {
  const id = document.querySelector(".note.active")?.dataset.id;
  if (!id) return;
  const note = notesCache.find(n => n.id == id);
  enterEditMode(note);
});

document.getElementById("cancel-btn").addEventListener("click", () => {
  exitEditMode();
});

document.getElementById("save-btn").addEventListener("click", async () => {
  const id = document.querySelector(".note.active")?.dataset.id;
  const title = document.getElementById("edit-title").value;
  const content = document.getElementById("edit-text").value;
  const res = await fetch(`/api/notes/${id}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": getCSRFToken()
    },
    body: JSON.stringify({ title, content })
  });
  if (!res.ok) return alert("Error saving note");
  const updated = await res.json();
  const index = notesCache.findIndex(n => n.id == id);
  notesCache[index] = updated;
  renderNotes(notesCache);
  openNote(id);
});

document.querySelector(".new-note-btn").addEventListener("click", async () => {
  if (!currentSubjectId) {
    alert("Select a subject first");
    return;
  }

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
  notesCache.unshift(newNote);
  renderNotes(notesCache);

  openNote(newNote.id);
  enterEditMode(newNote);
});


// Add subject --------------------------------------------------
const addSubjectBtn = document.getElementById("add-subject");

addSubjectBtn.addEventListener("click", () => {
  // Prevent multiple input blocks
  if (subjectsList.querySelector(".new-subject-input")) return;

  const li = document.createElement("li");
  li.className = "subject new-subject-input";
  li.innerHTML = `
  <div class="subject-main" style="display:flex;align-items:center;gap:8px;flex:1;">
    <span class="dot dot-blue"></span>
    <input type="text" class="subject-input" placeholder="New subject..." autofocus>
  </div>
`;

  //subjectsList.appendChild(li);
  subjectsList.prepend(li);
  const input = li.querySelector("input");
  input.focus();

  const cancel = () => li.remove();

  // Save on Enter
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

  // Cancel if loses focus
  input.addEventListener("blur", () => {
    setTimeout(cancel, 120);
});
});
document.getElementById("close-btn").addEventListener("click", () => {
  hideViewer();
  document.querySelectorAll(".note").forEach(n => n.classList.remove("active"));
});

document.getElementById("delete-note-btn").addEventListener("click", async () => {
  const id = document.querySelector(".note.active")?.dataset.id;
  if (!id) return;
  if (!confirm("Delete this note?")) return;

  const res = await fetch(`/api/notes/${id}`, {
    method: "DELETE",
    headers: { "X-CSRFToken": getCSRFToken() },
  });

  if (!res.ok) return alert("Error deleting note");

  // remove from cache and re-render
  notesCache = notesCache.filter(n => n.id != id);
  renderNotes(notesCache);
  hideViewer();
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
