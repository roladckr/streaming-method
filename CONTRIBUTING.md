# Contributing

We all work on the same repository. To avoid stepping on each other's code, we follow
this workflow. Golden rule: **never work directly on `main`**.

---

## 1. First time only

Clone the repository to your machine:

```bash
git clone https://github.com/Cr4shmars/streaming-method.git
cd streaming-method
```

Set your name and email (the ones you use on GitHub):

```bash
git config user.name "Your Name"
git config user.email "you@example.com"
```

---

## 2. The everyday cycle

### a) Update your copy before starting

```bash
git checkout main
git pull
```

### b) Create a branch for what you are about to do

One branch per task. Keep the name short and descriptive:

```bash
git checkout -b feat/chunked-reader
```

Prefixes we use:
| Prefix | Purpose |
|---|---|
| `feat/` | New functionality |
| `fix/` | Bug fix |
| `docs/` | Documentation |
| `refactor/` | Restructuring code without changing behavior |

### c) Work and save your changes

```bash
git add .
git commit -m "feat: read the spreadsheet in batches of 1000 rows"
```

Small, frequent commits beat one giant commit at the end.

### d) Push your branch

```bash
git push -u origin feat/chunked-reader
```

### e) Open a Pull Request (PR)

GitHub shows a "Compare & pull request" button when you open the repo.
Describe **what you did and why**. Ask a teammate to review it.

### f) After it is approved and merged

```bash
git checkout main
git pull
git branch -d feat/chunked-reader
```

---

## 3. Team rules

1. **Do not commit data files.** Test spreadsheets go in `data/`, which git ignores.
   GitHub rejects files larger than 100 MB.
2. **Do not commit credentials.** No tokens, passwords, or API keys. Use `.env`
   (also ignored) and document the required variables in `.env.example`.
3. **One PR, one thing.** Reviewing 50 lines is far easier than reviewing 800.
4. **If something breaks, say so.** Better to ask than to break `main`.

---

## 4. Common problems

**"I got a merge conflict"**
Someone edited the same lines you did. Bring main's changes into your branch and
resolve them:

```bash
git checkout main
git pull
git checkout your-branch
git merge main
```

Git marks conflicts inside the files with `<<<<<<<` and `>>>>>>>`. Edit the file, keep
the correct version, delete the markers, then:

```bash
git add .
git commit
```

**"I committed to main by mistake"**
If you have not pushed yet, move the commit onto a new branch:

```bash
git branch my-new-branch
git reset --hard origin/main
git checkout my-new-branch
```

⚠️ `reset --hard` discards uncommitted changes. Make sure your work is already in the
commit before running it.

**"I don't know what state I'm in"**

```bash
git status
```

This is the command you will use most. It tells you which branch you are on and what
changes you have.
