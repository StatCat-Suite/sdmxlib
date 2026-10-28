([Français](#comment-contribuer))

## How to contribute

Open an [issue](https://github.com/StatCat-Suite/sdmxlib/issues) to report a
bug or propose a change, and discuss it there before writing a large pull
request. If you do not have write access, GitHub forks the repository when you
edit a file, and you send the pull request from your fork.

Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md).

### Security

**Do not report a security issue in public.** See [SECURITY.md](SECURITY.md).

### Before you open a pull request

- **Run `just ci` and read its last line.** It is the gate, and it runs on
  your machine: hosted CI only publishes the documentation. `just check`
  (lint and type check) and `just test` are the faster loop while you work.
- **Write commits as conventional commits** that name the issue:
  `fix(query): keep the limit when refining a query (GH-12)`. The type decides
  where the change lands in the changelog. A `!` after the type or scope marks
  a breaking change, which also needs an entry in `docs/upgrading.md`.
- **Fix a lint or type finding in the code.** A suppression comment (`# noqa`,
  `# pyrefly: ignore[...]`) needs the maintainer's agreement, and
  `ruff.toml` and `pyrefly.toml` are changed only after discussion.
- **Write plainly** in comments, docstrings, docs and commit messages, as the
  "Writing style" section of [CLAUDE.md](CLAUDE.md) describes. Docstrings are
  Google style.

[CLAUDE.md](CLAUDE.md) holds the full set of project rules: architecture,
testing, typing and SQL style.

______________________

## Comment contribuer

Ouvrez un [enjeu (issue)](https://github.com/StatCat-Suite/sdmxlib/issues) pour
signaler un bogue ou proposer une modification, et discutez-en avant de
rédiger une demande de tirage (pull request) importante. Si vous n'avez pas
accès en écriture, GitHub crée une copie (fork) du dépôt lorsque vous modifiez
un fichier, et vous envoyez la demande de tirage depuis cette copie.

Toute personne qui participe suit le [Code de conduite](CODE_OF_CONDUCT.md#code-de-conduite).

### Sécurité

**Ne signalez aucun problème de sécurité en public.** Voir
[SECURITY.md](SECURITY.md#sécurité).

### Avant d'ouvrir une demande de tirage

- **Exécutez `just ci` et lisez sa dernière ligne.** C'est le contrôle
  d'intégration, et il s'exécute sur votre machine : l'intégration continue
  hébergée ne fait que publier la documentation. `just check` (analyse
  statique et vérification des types) et `just test` sont la boucle plus
  rapide pendant le travail.
- **Rédigez les commits selon les Conventional Commits** en nommant l'enjeu :
  `fix(query): keep the limit when refining a query (GH-12)`. Le type
  détermine la section du journal des modifications. Un `!` après le type ou
  la portée indique un changement incompatible, qui exige aussi une entrée
  dans `docs/upgrading.md`.
- **Corrigez dans le code ce que signalent l'analyse statique et la
  vérification des types.** Un commentaire de suppression (`# noqa`,
  `# pyrefly: ignore[...]`) exige l'accord du responsable, et `ruff.toml` et
  `pyrefly.toml` ne sont modifiés qu'après discussion.
- **Écrivez simplement** dans les commentaires, les docstrings, la
  documentation et les messages de commit, comme le décrit la section
  « Writing style » de [CLAUDE.md](CLAUDE.md). Les docstrings suivent le style
  Google.

[CLAUDE.md](CLAUDE.md) réunit l'ensemble des règles du projet : architecture,
tests, typage et style SQL. Il est rédigé en anglais.
