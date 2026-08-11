# Streaming Method

A streaming approach for processing large Excel/CSV files in [n8n](https://n8n.io),
working around the memory limits n8n hits when loading heavy files all at once.

## The problem

n8n loads binary files fully into memory. With a spreadsheet of hundreds of thousands
of rows, the workflow either runs out of memory or takes far too long. The goal of this
repository is to read the file **in chunks** and emit batches of rows, instead of
loading everything up front.

## Status

🚧 Work in progress. See [CONTRIBUTING.md](CONTRIBUTING.md) for how to work in this repo.

## Structure

```
.
├── README.md          # This file
├── CONTRIBUTING.md    # How to collaborate: branches, commits, pull requests
├── src/               # Source code
├── docs/              # Documentation and notes
└── data/              # Test files (NOT committed to git)
```

## Requirements

- Node.js 18 or later
- n8n (local or cloud)

## Team

- Marcos ([@Cr4shmars](https://github.com/Cr4shmars))
- _(add your names here)_
