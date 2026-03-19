
document.addEventListener("DOMContentLoaded", function () {
  // ----------------------------
  // Helper for CSRF token
  // ----------------------------
  function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== "") {
      const cookies = document.cookie.split(";");
      for (let i = 0; i < cookies.length; i++) {
        const cookie = cookies[i].trim();
        if (cookie.substring(0, name.length + 1) === name + "=") {
          cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
          break;
        }
      }
    }
    return cookieValue;
  }

  // ----------------------------
  // Bind checkboxes
  // ----------------------------
  function bindCheckboxes() {
    document.querySelectorAll(".task-checkbox").forEach(function (checkbox) {
      // prevent duplicate listeners
      if (checkbox.dataset.bound === "true") return;
      checkbox.dataset.bound = "true";

      checkbox.addEventListener("change", function () {
        if (!checkbox.checked) return;
        const taskId = checkbox.getAttribute("data-task-id");
        if (!taskId) return;

        const csrftoken = getCookie("csrftoken");

        const data = new URLSearchParams();
        data.append("task_id", taskId);

        fetch("/tasks/complete/", {
          method: "POST",
          headers: {
            "X-CSRFToken": csrftoken,
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded",
          },
          body: data,
        })
          .then((response) => response.json())
          .then((data) => {
            if (data.success) {
              const li = document.getElementById(`task-${taskId}`);
              if (li) {
                li.classList.add("fade-out");
                setTimeout(() => li.remove(), 500);
              }
            } else {
              alert("Failed to complete task: " + (data.error || "Unknown error"));
              checkbox.checked = false;
            }
          })
          .catch(() => {
            alert("Network error: could not mark task as complete.");
            checkbox.checked = false;
          });
      });
    });
  }

  // ----------------------------
  // Bind anchor buttons
  // ----------------------------
  function bindAnchorButtons() {
    document.querySelectorAll(".anchor-btn").forEach(function (btn) {
      // prevent duplicate listeners
      if (btn.dataset.bound === "true") return;
      btn.dataset.bound = "true";

      btn.addEventListener("click", function (e) {
        e.preventDefault();
        const taskId = btn.getAttribute("data-task-id");
        if (!taskId) return;

        fetch(`/tasks/${taskId}/toggle_anchor/`, {
          method: "POST",
          headers: {
            "X-CSRFToken": getCookie("csrftoken"),
            "X-Requested-With": "XMLHttpRequest",
          },
        })
          .then((res) => res.json())
          .then((data) => {
            if (data.success) {
              btn.innerHTML = data.anchored
                ? '<span class="anchored" title="Anchored">📌</span>'
                : '<span class="not-anchored" title="Click to anchor">📍</span>';
            } else {
              alert(data.error || "Failed to toggle anchor. Please try again.");
            }
          })
          .catch(() => alert("Network error: could not toggle anchor."));
      });
    });
  }

  // ----------------------------
  // Refresh tasks list
  // ----------------------------
  function formatDue(task) {
    if (!task.due_date) return "";
    return task.due_time ? `Due ${task.due_date}, ${String(task.due_time).slice(0, 5)}` : `Due ${task.due_date}`;
  }

  window.refreshTasks = function () {
    console.log("refreshTasks() called");
    fetch("/api/tasks/", { credentials: "same-origin" })
      .then((res) => {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then((data) => {
        // DAILY TASKS
        const dailyList = document.getElementById("daily-tasks-list");
        if (dailyList) {
          dailyList.innerHTML = "";
          if (data.daily_tasks.length === 0) {
            dailyList.innerHTML = "<li>No daily tasks. Enjoy your day!</li>";
          } else {
            data.daily_tasks.forEach((t) => {
              const li = document.createElement("li");
              li.id = `task-${t.id}`;
              li.innerHTML = `
                <label>
                  <input type="checkbox" class="task-checkbox" data-task-id="${t.id}">
                  <span class="task-title">${t.title}</span>
                </label>
                <div class="task-meta-row">
                  ${t.reminder_enabled ? '<span class="task-pill task-pill-reminder">Reminder set</span>' : ""}
                </div>
                ${t.description ? `<div class="task-desc">${t.description}</div>` : ""}
                <button class="anchor-btn" data-task-id="${t.id}" aria-label="Toggle anchor">
                  ${t.is_anchored
                    ? '<span class="anchored" title="Anchored">📌</span>'
                    : '<span class="not-anchored" title="Click to anchor">📍</span>'}
                </button>
                <a href="/tasks/${t.id}/edit/">Edit</a>
                <a href="/tasks/${t.id}/delete/">Delete</a>
              `;
              dailyList.appendChild(li);
            });
          }
        }

        // LONG-TERM TASKS
        const longList = document.getElementById("long-tasks-list");
        if (longList) {
          longList.innerHTML = "";
          if (data.long_tasks.length === 0) {
            longList.innerHTML = "<li>No long-term tasks yet.</li>";
          } else {
            data.long_tasks.forEach((t) => {
              const li = document.createElement("li");
              li.id = `task-${t.id}`;
              li.innerHTML = `
                <label>
                  <input type="checkbox" class="task-checkbox" data-task-id="${t.id}">
                  <span class="task-title">${t.title}</span>
                </label>
                <div class="task-meta-row">
                  ${formatDue(t) ? `<span class="task-pill">${formatDue(t)}</span>` : ""}
                  ${t.reminder_enabled ? '<span class="task-pill task-pill-reminder">Reminder set</span>' : ""}
                </div>
                ${t.description ? `<div class="task-desc">${t.description}</div>` : ""}
                <a href="/tasks/${t.id}/edit/">Edit</a>
                <a href="/tasks/${t.id}/delete/">Delete</a>
              `;
              longList.appendChild(li);
            });
          }
        }

        // After rebuilding lists, rebind checkboxes + anchors
        bindCheckboxes();
        bindAnchorButtons();
      })
      .catch((err) => console.error("refreshTasks failed:", err));
  };

  // ----------------------------
  // Show/Hide Add Task Forms
  // ----------------------------
  function showForm(formId) {
    document.getElementById(formId).style.display = "block";
    if (formId === "daily-form-container") {
      document.getElementById("long-form-container").style.display = "none";
    } else {
      document.getElementById("daily-form-container").style.display = "none";
    }
  }
  function hideForm(formId) {
    document.getElementById(formId).style.display = "none";
  }

  function syncReminderCard(formEl) {
    const reminderEnabled = formEl.querySelector('input[name$="reminder_enabled"]');
    const dueDateInput = formEl.querySelector('input[name$="due_date"]');
    const reminderTime = formEl.querySelector('input[name$="reminder_time"]');
    const reminderFields = formEl.querySelector(".reminder-fields");
    const isLongTerm = !!dueDateInput && dueDateInput.type !== "hidden";
    const canConfigure = !!reminderEnabled && reminderEnabled.checked && (!isLongTerm || !!dueDateInput.value);

    if (reminderFields) {
      reminderFields.style.opacity = canConfigure ? "1" : "0.55";
    }
    if (reminderTime) reminderTime.disabled = !canConfigure;
  }

  function bindReminderForms() {
    document.querySelectorAll(".add-task-form, .edit-task-form").forEach((formEl) => {
      const reminderEnabled = formEl.querySelector('input[name$="reminder_enabled"]');
      const dueDateInput = formEl.querySelector('input[name$="due_date"]');

      if (reminderEnabled && reminderEnabled.dataset.bound !== "true") {
        reminderEnabled.dataset.bound = "true";
        reminderEnabled.addEventListener("change", () => syncReminderCard(formEl));
      }

      if (dueDateInput && dueDateInput.dataset.bound !== "true") {
        dueDateInput.dataset.bound = "true";
        dueDateInput.addEventListener("change", () => syncReminderCard(formEl));
      }

      syncReminderCard(formEl);
    });
  }

  var showDailyBtn = document.getElementById("show-daily-form");
  var showLongBtn = document.getElementById("show-long-form");
  if (showDailyBtn) {
    showDailyBtn.onclick = function () {
      showForm("daily-form-container");
    };
  }
  if (showLongBtn) {
    showLongBtn.onclick = function () {
      showForm("long-form-container");
    };
  }

  var closeDailyBtn = document.getElementById("close-daily-form");
  var closeLongBtn = document.getElementById("close-long-form");
  if (closeDailyBtn) {
    closeDailyBtn.onclick = function () {
      hideForm("daily-form-container");
    };
  }
  if (closeLongBtn) {
    closeLongBtn.onclick = function () {
      hideForm("long-form-container");
    };
  }

  // ----------------------------
  // Initial bindings
  // ----------------------------
  bindCheckboxes();
  bindAnchorButtons();
  bindReminderForms();
});
