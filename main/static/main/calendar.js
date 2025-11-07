(function () {
  const $ = (s, r = document) => r.querySelector(s);

  // CSRF helper for Django
  function getCookie(name) {
    const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
    return m ? decodeURIComponent(m.pop()) : null;
  }
  const CSRF = () => ({ "X-CSRFToken": getCookie("csrftoken") });

  // ======== MODAL ELEMENTS ========
  const modal = $("#event-modal");
  const backdrop = $("#event-modal-backdrop");
  const modalClose = $("#event-modal-close");
  const form = $("#event-form");
  const idIn = $("#ev-id");
  const titleIn = $("#ev-title");
  const descIn = $("#ev-desc");
  const startIn = $("#ev-start");
  const endIn = $("#ev-end");
  const allDayIn = $("#ev-all-day");
  const cancelBtn = $("#event-cancel");
  const deleteBtn = $("#event-delete");
  const submitBtn = $("#event-submit");
  const modalTitle = $("#event-modal-title");

  let mode = "create";           // "create" | "edit"
  let currentFcEvent = null;     // FullCalendar EventApi when editing



function toLocalInputValue(date) {
if (!(date instanceof Date)) date = new Date(date);
if (isNaN(date)) return "";
const pad = n => String(n).padStart(2, "0");
const y = date.getFullYear();
const m = pad(date.getMonth() + 1);
const d = pad(date.getDate());
const hh = pad(date.getHours());
const mm = pad(date.getMinutes());
// local-naive string for <input type="datetime-local">
return `${y}-${m}-${d}T${hh}:${mm}`;
}



function isoToLocalDatetimeInput(isoLike) {
  // Works for: 2025-09-29T12:00, 2025-09-29T12:00:00, 2025-09-29T12:00:00+03:00, 2025-09-29T09:00:00Z
  if (typeof isoLike === "string") {
    const m = isoLike.match(/^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})/);
    if (m) return m[1]; // keep wall HH:MM exactly as the calendar shows
  }
  // fallback for Date or anything else
  return toLocalInputValue(isoLike);
}

function setInputValueFromAny(inputEl, valueAny) {
  if (typeof valueAny === "string") {
    inputEl.value = isoToLocalDatetimeInput(valueAny);
  } else {
    const d = (valueAny instanceof Date) ? valueAny : new Date(valueAny);
    inputEl.value = toLocalInputValue(d);
  }
}





  function openModal(initial, nextMode = "create", fcEvent = null) {
    mode = nextMode;
    currentFcEvent = fcEvent;

    idIn.value = initial?.id ?? "";
    titleIn.value = initial?.title ?? "";
    descIn.value = initial?.description ?? "";
    allDayIn.checked = !!initial?.allDay;

    const now = new Date();
    const startAny = initial?.start ?? now;
    const endAny = initial?.end ?? new Date(now.getTime() + 60 * 60 * 1000);

    setInputValueFromAny(startIn, startAny);
    setInputValueFromAny(endIn, endAny);

    // UI mode toggles
    modalTitle.textContent = (mode === "create") ? "New Event" : "Edit Event";
    submitBtn.textContent = (mode === "create") ? "Create" : "Save";
    deleteBtn.classList.toggle("hidden", mode !== "edit");

    backdrop.classList.remove("hidden");
    modal.classList.remove("hidden");
  }
  function closeModal() {
    modal.classList.add("hidden");
    backdrop.classList.add("hidden");
  }
  modalClose?.addEventListener("click", closeModal);
  cancelBtn?.addEventListener("click", closeModal);
  backdrop?.addEventListener("click", closeModal);

  // ======== API helpers ========
  async function createEvent() {
    const payload = {
      title: titleIn.value.trim(),
      description: descIn.value.trim(),
      start: startIn.value,
      end: endIn.value,
      allDay: allDayIn.checked,
    };
    console.log("CREATE sending:", payload);
    const r = await fetch("/api/events/", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...CSRF() },
      credentials: "same-origin",
      body: JSON.stringify(payload),
    });
    if (!r.ok) throw new Error(await r.text() || "Failed creating event");
    return r.json();
  }

  async function patchEvent(id, partial) {
    console.log("PATCH sending start:", partial.start, "end:", partial.end);

    const r = await fetch(`/api/events/${id}/`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", ...CSRF() },
      credentials: "same-origin",
      body: JSON.stringify(partial),
    });
    if (!r.ok) throw new Error(await r.text() || "Failed updating event");
    return r.json();
  }

  async function deleteEvent(id) {
    const r = await fetch(`/api/events/${id}/`, {
      method: "DELETE",
      headers: { ...CSRF() },
      credentials: "same-origin",
    });
    if (!r.ok && r.status !== 204) throw new Error(await r.text() || "Failed deleting event");
  }




  // ======== TASK PANEL ========
  const panel = $("#day-panel");
  const panelTitle = $("#day-panel-title");
  const panelClose = $("#day-panel-close");
  const dailyList = $("#daily-list");
  const longtermList = $("#longterm-list");

  panelClose?.addEventListener("click", () => panel.classList.add("hidden"));

  async function loadDay(dateISO) {
    const r = await fetch(`/api/tasks/day/?date=${encodeURIComponent(dateISO)}`, { credentials: "same-origin" });
    if (!r.ok) throw new Error("tasks_of_day endpoint not ready");
    return await r.json();
  }

  function renderDaySkeleton(dateISO) {
    if (panelTitle) panelTitle.textContent = new Date(dateISO).toDateString();
    if (dailyList) dailyList.innerHTML = "";
    if (longtermList) longtermList.innerHTML = "";
    panel?.classList.remove("hidden");
  }

  function renderDay(data) {
    if (panelTitle) panelTitle.textContent = new Date(data.date).toDateString();

    // Daily tasks
    if (dailyList) {
      dailyList.innerHTML = "";
      data.daily.forEach(t => {
        const li = document.createElement("li");
        const id = `d-${t.id}`;
        li.innerHTML = `
          <label for="${id}">
            <input type="checkbox" id="${id}" ${t.completed ? "checked" : ""}>
            <span>${t.title}</span>
          </label>
        `;
        const cb = li.querySelector("input");
        cb.addEventListener("change", async () => {
          const method = cb.checked ? "POST" : "DELETE";
          const form = new URLSearchParams({ task_id: t.id, date: data.date });
          const r = await fetch("/api/daily-completions/", {
            method,
            headers: { "Content-Type": "application/x-www-form-urlencoded", ...CSRF() },
            credentials: "same-origin",
            body: form.toString()
          });
          if (!r.ok) { cb.checked = !cb.checked; alert("Failed saving daily completion"); }
        });
        dailyList.appendChild(li);
      });
    }
    const isCompleted = !!t.completed_on_or_before_selected_day;
    // Long-term tasks
    if (longtermList) {
      longtermList.innerHTML = "";
      data.long_term.forEach(t => {
        const li = document.createElement("li");
        const id = `lt-${t.id}`;
        li.innerHTML = `
          <label for="${id}">
            <input type="checkbox" id="${id}" ${isCompleted ? "checked" : ""}>
            <span>${t.title}</span>
          </label>
        `;
        const cb = li.querySelector("input");
        cb.addEventListener("change", async () => {
          const method = cb.checked ? "PATCH" : "DELETE";
          const qs = cb.checked ? `?date=${encodeURIComponent(data.date)}` : "";
          const r = await fetch(`/api/tasks/${t.id}/complete/${qs}`, {
            method,
            headers: { ...CSRF() },
            credentials: "same-origin"
          });
          if (!r.ok) { cb.checked = !cb.checked; alert("Failed updating long-term completion"); }
        });
        longtermList.appendChild(li);
      });
    }

    panel?.classList.remove("hidden");
  }

  // ======== CALENDAR INIT ========
  document.addEventListener("DOMContentLoaded", function () {
    const calendarEl = document.getElementById("calendar");
    if (!calendarEl) return;

    const pluginList = [];
    if (window.FullCalendar && FullCalendar.interactionPlugin) {
      pluginList.push(FullCalendar.interactionPlugin);
    }

    const calendar = new FullCalendar.Calendar(calendarEl, {
      ...(pluginList.length ? { plugins: pluginList } : {}),

      initialView: "dayGridMonth",
      headerToolbar: {
        left: "prev,next",
        center: "title",
        right: "addEvent dayGridMonth,timeGridWeek,timeGridDay"
      },
      customButtons: {
        addEvent: {
          text: "New Event",
          click: () => openModal({ start: new Date(), end: new Date(Date.now() + 60 * 60 * 1000), allDay: false }, "create")
        }
      },
      firstDay: 0,
      timeZone: "Asia/Jerusalem",
      selectable: true,
      selectMirror: true,
      selectMinDistance: 2,
      unselectAuto: true,
      selectAllow: function(selectInfo) {
        const vt = calendar.view ? calendar.view.type : 'dayGridMonth';
        return (vt === 'timeGridDay' || vt === 'timeGridWeek') && selectInfo.start < selectInfo.end;
      },
      select: function(info) {
        openModal({ start: info.startStr, end: info.endStr, allDay: false }, "create");
      },

      // Make events editable
      editable: true,
      eventResizableFromStart: true,

      events: function(fetchInfo, success, failure) {
        const url = `/api/calendar/?start=${encodeURIComponent(fetchInfo.startStr)}&end=${encodeURIComponent(fetchInfo.endStr)}`;
        fetch(url, { credentials: "same-origin" })
          .then(r => r.ok ? r.json() : Promise.reject(new Error("Failed to load events")))
          .then(data => success(data))
          .catch(failure);
      },

      // Click to edit
      eventClick: function(info) {
        const e = info.event;
        openModal({
          id: e.id,
          title: e.title,
          description: e.extendedProps?.description || "",
          start: e.startStr,
          end: e.endStr || e.startStr, // ensure an end for the input
          allDay: e.allDay
        }, "edit", e);
      },

      // Drag or resize -> persist
      eventDrop: async function(info) {
        try {
          await patchEvent(info.event.id, {
          start: toLocalInputValue(info.event.start), // local "YYYY-MM-DDTHH:MM"
          end: info.event.end ? toLocalInputValue(info.event.end) : null,
          allDay: !!info.event.allDay
          });
        } catch (err) {
          info.revert();
          alert(err.message || "Failed to save move");
        }
      },
      eventResize: async function(info) {
        try {
          await patchEvent(info.event.id, {
          start: toLocalInputValue(info.event.start),
          end: info.event.end ? toLocalInputValue(info.event.end) : null,
          allDay: !!info.event.allDay
          });
        } catch (err) {
          info.revert();
          alert(err.message || "Failed to save resize");
        }
      },

      dateClick: async function (info) {
        const vt = calendar.view ? calendar.view.type : 'dayGridMonth';
        if (vt !== 'dayGridMonth') return;
        document.querySelectorAll('.fc-daygrid-day').forEach(el => el.classList.remove('fc-day-selected'));
        info.dayEl.classList.add('fc-day-selected');
        const isoDate = info.dateStr;
        renderDaySkeleton(isoDate);
        try {
          const data = await loadDay(isoDate);
          renderDay(data);
        } catch {}
      }
    });

    calendar.render();
    window.calendar = calendar;
    // Form submit (create or save edit)
    form?.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        if (mode === "create") {
          const ev = await createEvent();
          // include description in extendedProps if returned
          calendar.addEvent({
            id: ev.id, title: ev.title, start: ev.start, end: ev.end, allDay: ev.allDay,
            extendedProps: { description: ev.description || "" }
          });
        } else {
          const id = idIn.value;
          const updated = await patchEvent(id, {
            title: titleIn.value.trim(),
            description: descIn.value.trim(),
            start: startIn.value, // send local naive string
            end: endIn.value,     // send local naive string
            allDay: allDayIn.checked
          });

          if (currentFcEvent) {
            currentFcEvent.setProp("title", updated.title);
            currentFcEvent.setStart(updated.start);
            currentFcEvent.setEnd(updated.end);
            currentFcEvent.setAllDay(!!updated.allDay);
            if (currentFcEvent.setExtendedProp) {
              currentFcEvent.setExtendedProp("description", updated.description || "");
            }
          } else {
            // fallback if we somehow lost the event reference
            calendar.refetchEvents();
          }
        }
        closeModal();
      } catch (err) {
        alert(err.message || "Failed saving event");
      }
    });

    // Delete (icon button)
    deleteBtn?.addEventListener("click", async () => {
      if (mode !== "edit") return;
      const id = idIn.value;
      try {
        await deleteEvent(id);
        // remove from calendar UI
        if (currentFcEvent) currentFcEvent.remove();
        closeModal();
      } catch (err) {
        alert(err.message || "Failed deleting event");
      }
    });
  });
})();
