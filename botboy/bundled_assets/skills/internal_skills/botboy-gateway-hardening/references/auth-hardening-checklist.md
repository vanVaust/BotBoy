# Auth Hardening Checklist

Walk these paths in order:

1. login
2. refresh
3. protected route without credentials
4. protected route with valid credentials
5. API key issuance and use
6. principal listing and mutation
7. rate-limit behavior on repeated calls

Verify all of the following:

- correct status semantics
- correct principal propagation
- correct request ID behavior
- correct route protection boundary
- correct parity between stdlib and FastAPI where the product promises it
