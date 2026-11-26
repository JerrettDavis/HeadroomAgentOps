# Implementation language

Use .NET for orchestration, service launchers, automation and helper tooling.
Use Python only when .NET cannot otherwise reasonably perform the task; explain
the concrete limitation before introducing new Python tooling. Convenience or
familiarity alone does not justify Python.

Reduce Python incrementally as work touches existing functionality. When
changing a Python component or nearby behavior, assess whether .NET can
reasonably replace that functionality within the task's scope and prefer that
replacement when practical. Preserve behavior and verification.

Do not start unrelated migration work, proactive refactors or a wholesale
rewrite. Existing Python application code and its native test suite may still
be maintained and verified where replacement is not reasonable for the task.
