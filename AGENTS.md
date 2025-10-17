# Ghostship Maintenance Notes

- Run `python forum_simulator/manage.py test` before submitting changes so the
  embedded Django project stays green.
- Prefer small, readable improvements that reduce duplicate database work or
  eliminate stale code paths. Leave destructive refactors for dedicated tasks.
- When adjusting templates under `forum/templates/forum/`, extend the organic
  layout variants (`ol_*.html`) unless a view explicitly requires a legacy
  structure.
- Document behaviour tweaks in `forum_simulator/README.md` or adjacent docs when
  they affect local development or operational workflows.
