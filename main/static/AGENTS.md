# Design Aesthetics & UI Guidelines

This file is a reference for AI agents to maintain a consistent UI aesthetic when modifying or designing new feature pages for TaskIt. It documents the "Plant Care" design system applied across Tasks, Calendar, and Notes.

## 1. Color Palette (Theme Tokens)

All new CSS files should follow this core variable structure to maintain uniformity:
```css
:root {
  --bg-app: #f8f9fa;               /* Global app background (light gray) */
  --surface: #ffffff;              /* Elevated card background (pure white) */
  
  --text-main: #1f2937;            /* Primary dark charcoal text */
  --text-muted: #6b7280;           /* Secondary gray text */
  
  --accent: #22c55e;               /* Brand vibrant green */
  --accent-hover: #16a34a;         /* Slightly darker green for hover states */
  --border: #e5e7eb;               /* Standard subtle border color */
  
  --highlight-pale: #f2fceb;       /* Extremely soft green for active selection highlights */
  --shadow-sm: 0 4px 6px rgba(0,0,0,.05); /* Soft, airy drop shadow */
}
```

## 2. Layout & Structure

- **Airy Background**: The `body` or main application area should always use the `--bg-app` light gray.
- **Elevated Cards**: Main content containers, side panels, and tables must be raised onto `--surface` (white) with `--shadow-sm` and a `1px solid var(--border)`. 
- **Corners**: Use `border-radius: 12px;` or `16px` for large main cards (like the calendar component, note viewer, or modal). Use `border-radius: 8px;` for inner elements like inputs, dropdowns, and buttons.
- **Density**: Use generous padding. Elements should feel breathable, not cluttered.

## 3. Interactive Components

### Buttons
- **Primary Buttons (Call to Action)**: 
  - Background: `var(--accent)`
  - Text: White, bold/semibold (`font-weight: 500` or `600`).
  - Shape: Depending on location, use pill-shaped (`border-radius: 9999px`) or rectangular (`border-radius: 8px`).
  - Hover: `var(--accent-hover)`
- **Secondary/Light Buttons (Edit, Close, Cancel, Filters)**:
  - Background: `var(--surface)` or transparent.
  - Border: `1px solid var(--border)`.
  - Text: `var(--text-main)`.
  - Hover: Use `#f3f4f6` (light gray highlight) with no harsh border changes.
- **Focus Rings**:
  - Avoid defaulting to heavy browser focus rings (e.g. standard blue box-shadows).
  - Strip focus drop shadows on toggle buttons (`box-shadow: none !important`), or use a custom soft green ring for inputs:
    `box-shadow: 0 0 0 3px rgba(34, 197, 94, 0.15); border-color: rgba(34, 197, 94, 0.5);`

### Form Inputs
- **Base State**: `1px solid var(--border)`, `border-radius: 8px`, `background: var(--surface)`.
- **Focus State**: MUST apply the soft green focus ring defined above. Outline must be `none`.
- Use `color: var(--text-main);` to avoid default browser grays in textareas and inputs.

### Icons
- **SVG Line Art**: Always replace legacy text labels (e.g. "Edit", "X") or Emojis ("🗑") with minimal SVG icons. 
- Specifically, use Lucide line icons with a stroke-width of 2, `fill="none"`, and `stroke="currentColor"`.
- The SVG size should be kept between `14px` and `18px` so they fit gracefully within buttons without overwhelming the layout.
- For destructive icons (like delete), hover states should set the color to a soft red (`color: #b91c1c;` with background `#fef2f2`).

## 4. Specific View Rules
- **FullCalendar**: Ensure default blue event backgrounds are overridden to `var(--accent)`. Highlight logic must bind to the `--fc-today-bg-color`.
- **Task/Checklist Toggles**: Custom checkboxes should hide default `-webkit-appearance` and display as empty outlined boxes (`border: 1.5px solid #9ca3af`), becoming green checkboxes when active.
- **Modals**: Must sit on a black backdrop with `0.5` opacity. The modal itself should be a white surface, deeply rounded (`16px`), with an ultra-soft shadow (`box-shadow: 0 10px 30px rgba(17, 24, 39, 0.10);`).
