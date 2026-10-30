# PROJECT_RUNTIME.md

How a project's product finds its files, settings and other projects at runtime. Adopting Agent Traffic Control shapes how a project is developed; it never becomes something the product needs to run.

```text
remove every ATC artifact from a project -> the product still runs
```

## Runtime never reads ATC

The product — its applications, CLIs, scheduled tasks and the child processes they start — reads none of:

- the workspace map (`workspace.yaml`) or any other file of the ATC checkout;
- `ATC_ROOT` or any other environment variable ATC defines;
- the vendored `.atc/` directory, rendered skill copies, or the ATC policy block of `AGENTS.md`;
- the workspace layout (`projects/`, `worktrees/`, `state/`) as a way to derive a path.

Those artifacts serve development sessions only.

## The project owns its locations file

A product that needs machine-specific paths reads them from a locations file whose schema the project's own specification defines:

- the file declares its schema as `schema: <project>-locations/<version>`; the project raises the version, and the product refuses a version it does not support;
- each entry is an absolute path the product uses, named for what the product does with it, never for where ATC puts it;
- a dependency on another project's output is its own entry (for example the directory the product reads results from), never a path derived from that project's repository or registration;
- how the product finds the file is part of the project's specification; when the file is missing or invalid the product stops with an error instead of guessing a default location.

A project may follow these rules by restating them in its own specification; it never references this module or an ATC schema at runtime.

## ATC produces, the project consumes

ATC tooling may write a project's locations file from the workspace map, emitting the schema version the project declares. The product cannot tell whether ATC or the owner wrote it. When the project raises its schema version, the producer follows; a producer that lags makes the product refuse the file, never run on a guess.

## Safety checks use project information

A check the product makes about its paths — state disjoint from its repository, no Git checkout above its state, no links on a path — uses the locations file and the product's own location only. It never needs the workspace map to pass.

## Proving independence

A project that adopts these rules keeps a test that runs its main flow in a directory holding no ATC artifact, with no ATC environment variable set, given only a locations file.
