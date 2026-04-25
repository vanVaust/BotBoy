# Validation Hooks

Validate reflection changes through at least one of these surfaces:

- eval cases for the target failure mode
- trace spans showing the loop fired
- history or output changes visible to the operator
- regression tests for the new decision behavior

If the loop adds complexity without a measurable hook, tighten it.
