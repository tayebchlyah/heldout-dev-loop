# Coding standards

## Comments

Write a comment only for one of these kinds:

- a license header;
- behaviour that an outside dependency forces;
- a doc comment of a public API;
- a link to an issue or an RFC;
- a tool directive (for example a lint or type-checker pragma);
- a known shortcut, in the form `shortcut: <the limit>, <when to upgrade>`.

No other comments. The rule applies to new and changed lines only. No change removes old comments only to meet it.

A public API is what another module, another program or the user calls: a script's usage header, a CLI help text, a function that another module imports. A docstring of a private helper is not a public API doc comment.
