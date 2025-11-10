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

async function loadSubjects() {
  const res = await fetch("/api/subjects/");
  if (!res.ok) return;
  const subjects = await res.json();

  subjectsList.innerHTML = "";
  subjects.forEach(sub => {
    const li = document.createElement("li");
    li.className = "subject";
    li.dataset.id = sub.id;
    li.innerHTML = `<span class="dot dot-${sub.color || "gray"}"></span> ${sub.title}`;
    li.addEventListener("click", () => selectSubject(sub.id));
    subjectsList.appendChild(li);
  });

  if (subjects.length > 0) selectSubject(subjects[0].id);
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

  // Clear active highlight and set new one
  document.querySelectorAll(".note").forEach(n => n.classList.remove("active"));
  const el = notesList.querySelector(`[data-id="${id}"]`);
  if (el) el.classList.add("active");

  // Update viewer
  viewerTitle.textContent = note.title;
  viewerContent.textContent = note.content;

  // Ensure a clean non-edit state
  exitEditMode(); // hide edit fields, show viewer title/content
  showViewer();   // display the right panel
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

  // Immediately open in edit mode
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
  li.innerHTML = `<input type="text" class="subject-input" placeholder="New subject..." autofocus>`;
  subjectsList.appendChild(li);

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
  input.addEventListener("blur", cancel);
});



document.getElementById("close-btn").addEventListener("click", () => {
  hideViewer();
  document.querySelectorAll(".note").forEach(n => n.classList.remove("active"));
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

window.addEventListener("DOMContentLoaded", () => {
  loadSubjects();
});
