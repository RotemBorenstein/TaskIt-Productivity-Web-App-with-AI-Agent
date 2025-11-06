# TaskIt – Productivity Web-App with an AI Agent

**TaskIt** is a full-stack productivity web application built with **Django**, **PostgreSQL**, and **JavaScript (FullCalendar)**.  
It helps users organize tasks, schedule events, and track progress - all enhanced by an integrated **AI chat agent** powered by **LangChain** and the **OpenAI API**.

---

## Features

### Task Management
- Create and manage **daily** and **long-term** tasks.
- Mark tasks as complete; completed daily tasks reset each day.
- **Anchored tasks** automatically reappear the next day after completion.

### Calendar Integration
- Interactive **FullCalendar** view for events and deadlines.
- Create, edit, or drag-and-drop events directly on the calendar.
- View task completion status for any selected day.

### Statistics Dashboard
- Visualize completion rates by **day**, **week**, or **month**.
- Display top tasks not completed to highlight areas for improvement.
- Summarized performance analytics for quick progress tracking.

### AI Chat Agent
- Floating chat button opens an **in-app assistant**.
- Users can type natural-language commands like:
  - “Add a meeting tomorrow at 3 PM.”
  - “Create a long-term task to finish my project.”
  - “Show my completion rate this week.”
- Built using **LangChain** and the **OpenAI API**, with tools for:
  - Task creation (`add_task`)
  - Event creation (`add_event`)
  - Statistics analysis (`analyze_stats`)

---

## Tech Stack

**Backend:** Django, PostgreSQL, REST API  
**Frontend:** JavaScript, HTML, CSS, FullCalendar  
**AI Integration:** LangChain, OpenAI API (ChatGPT model)  
**Other Tools:** Chart.js, Python (timezone & date utilities), Django ORM

---

## Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/RotemBorenstein/TaskIt.git
   cd TaskIt
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Set up the database:**
   ```bash
   python manage.py migrate
   ```

4. **Add your environment variables:**
   Create a `.env` file in the project root:
   ```bash
   OPENAI_API_KEY=your_api_key_here
   ```

5. **Run the development server:**
   ```bash
   python manage.py runserver
   ```

6. **Access the app:**
   Open your browser and visit [http://localhost:8000](http://localhost:8000)

---

## Usage

- Navigate to the **Tasks** page to create and track daily or long-term tasks.
- Open the **Calendar** to view or create events interactively.
- Visit the **Stats** page to analyze completion performance.
- Click the **floating chat button** to open the AI agent and issue natural-language commands.

