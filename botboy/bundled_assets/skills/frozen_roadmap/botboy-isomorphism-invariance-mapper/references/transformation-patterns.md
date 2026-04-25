# Transformation Patterns

These transformations are common:

- split one module into several while preserving a contract
- merge two adjacent paths under one shared adapter
- rename structures while preserving roles
- replace transport or storage while keeping semantics
- move checks earlier or later in a flow while preserving outcome guarantees

If the transformation changes the invariant, it is not behavior-preserving.
