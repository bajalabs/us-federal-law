# Security

Report a vulnerability privately through GitHub: **Security → Report a vulnerability** on
this repository (private vulnerability reporting). Please do not open a public issue for it.

Include what is affected (the fetcher, the build, a release asset), how to reproduce it, and
the impact you see. You will get an acknowledgement, and a fix or an explanation, as soon as
the issue is understood.

The code downloads from public government hosts over HTTPS with certificate validation, and
checks every file against the sha256 in `manifests/`. It needs no credentials; the optional
`GOVINFO_API_KEY` is read from the environment and is never written to a file or a log.
