# Open LLM Vtuber

An Electron application with React and TypeScript

## Recommended IDE Setup

- [VSCode](https://code.visualstudio.com/) + [ESLint](https://marketplace.visualstudio.com/items?itemName=dbaeumer.vscode-eslint) + [Prettier](https://marketplace.visualstudio.com/items?itemName=esbenp.prettier-vscode)

## Project Setup

### Install

```bash
$ npm install
```

### Development

```bash
$ npm run dev
```

Web-only development against the Python backend:

```bash
$ npm run dev:web
```

### Quality

```bash
$ npm run lint
$ npm run typecheck
$ npm run build:web
```

`npm run lint` is a non-mutating quality gate. Use `npm run lint:fix` when you
explicitly want safe ESLint fixes applied.

### Build

```bash
# For windows
$ npm run build:win

# For macOS
$ npm run build:mac

# For Linux
$ npm run build:linux
```
