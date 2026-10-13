# Third-party components

The monitor binary uses Go's standard library and the BSD-3-Clause Go modules
`golang.org/x/net`, `golang.org/x/text`, `golang.org/x/term`, and `golang.org/x/sys`.
Their full copyright notices and license terms are distributed in `licenses.tar.gz`
and kept in the source repository's `third_party/` directory.

zju-connect is a separate, optional subprocess, downloaded on demand as an
unmodified official v1.3.1 release. It is licensed under AGPL-3.0. Its LICENSE and
SOURCE.txt are saved next to the downloaded core. Matching source:
https://github.com/Mythologyli/zju-connect/tree/5d7f5b11fcf231f72a0ec0d888bf0f2eadcce1da

Campus CAS behavior was adapted from this project's Python implementation,
which references the MIT-licensed SEUdaily campus_auth implementation.
