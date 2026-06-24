# Presentation: Vibe Coding, Safely

`claude-code-vibe-coding.md` is a [Marp](https://marp.app/)-compatible slide
deck on using Claude Code to build this Asana → Jira migration toolkit. Slides
are separated by `---`; per-slide **speaker notes** live in `<!-- ... -->` HTML
comment blocks (visible in presenter view, never on the slide).

## Render or present

Pick whichever fits — none of these add a dependency to this project.

- **VS Code:** install the *Marp for VS Code* extension, open the file, and use
  the preview / "Export slide deck…" command. Presenter view shows the notes.
- **Export to PDF / HTML / PPTX (one-off, no install):**
  ```bash
  npx @marp-team/marp-cli docs/presentation/claude-code-vibe-coding.md -o deck.pdf
  npx @marp-team/marp-cli docs/presentation/claude-code-vibe-coding.md -o deck.pptx
  ```
- **Google Slides:** export to `.pptx` (above), then *File → Import slides* in
  Google Slides.

## Editing

- Add a slide: insert a `---` separator and a new `##` heading.
- Add speaker notes: put an HTML comment block (`<!-- ... -->`) right after the
  slide's content.
- Every figure (test counts, module counts, commit hashes) is drawn from the
  repo as of the writing date — re-verify with `git log` / `find` before reusing
  in a later talk.
