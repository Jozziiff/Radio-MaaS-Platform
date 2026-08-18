// Monaco local bundling setup (M6.5): makes @monaco-editor/react use the
// locally-installed `monaco-editor` package instead of its default
// behavior of fetching Monaco from jsdelivr's CDN at runtime. This project
// has otherwise never depended on reaching the internet at runtime (k3d,
// MinIO, Vault all run locally) -- a CDN-loaded editor would be a new,
// silent exception to that: the create/edit macro form simply wouldn't
// render an editor if there's no internet connection at the moment the
// page loads. Imported once, before anything renders (see main.jsx).
//
// Imports `editor/editor.api` directly, not the package's root
// `monaco-editor` entry point -- the root entry eagerly pulls in Monarch
// tokenizers for every language Monaco ships (dozens: SQL, Ruby, Solidity,
// TypeScript's full language *service*, etc.), which inflated a single
// production JS chunk to 4.3MB for a form that only ever edits Python.
// `editor.api` is Monaco's own documented "core editor only, register
// languages yourself" entry point; only Python's Monarch tokenizer +
// language configuration (`languages/definitions/python`) is imported and
// registered below, so nothing edited by this app pulls in a language
// nobody uses.
//
// Only `editor.worker` is registered -- Monaco core ships a Monarch
// tokenizer for Python (enough for the syntax highlighting this component
// needs) but no dedicated Python *language service* worker the way it
// does for TypeScript/JSON/CSS/HTML, so those are deliberately not
// registered here; there's nothing to point them at.

// Relative paths reaching directly into node_modules, not bare package
// subpath specifiers -- monaco-editor's package.json `exports` map only
// declares its root entry point ("."), so strict ESM exports-map
// resolution (which this project's Vite 8/Rolldown enforces) rejects
// `monaco-editor/esm/vs/...` as a bare specifier even though the files
// exist on disk. Same reasoning as the worker URL below.
import { loader } from "@monaco-editor/react";
import * as monaco from "../node_modules/monaco-editor/esm/vs/editor/editor.api.js";
import { conf as pythonConf, language as pythonLanguage } from "../node_modules/monaco-editor/esm/vs/languages/definitions/python/python.js";

monaco.languages.register({ id: "python" });
monaco.languages.setLanguageConfiguration("python", pythonConf);
monaco.languages.setMonarchTokensProvider("python", pythonLanguage);

// `new URL(..., import.meta.url)` + native Worker, not the `?worker`
// import-suffix Vite plugin syntax Monaco's own docs usually show: this
// project's Vite 8 (Rolldown-based) fails to resolve `?worker` on a
// package-internal subpath at build time. A bare package-specifier form
// of this same pattern (`new URL("monaco-editor/esm/.../editor.worker.js",
// import.meta.url)`) also fails -- monaco-editor's package.json `exports`
// map only declares its root entry point ("."), not this deep subpath, so
// strict ESM `exports`-map resolution rejects it even though the file
// physically exists on disk. A path relative to this file (reaching
// through node_modules directly) sidesteps package-export resolution
// entirely -- the one part of this setup tied to this project's exact
// node_modules layout, not to a bundler quirk that might be fixed in a
// future Vite/rolldown release.
self.MonacoEnvironment = {
  getWorker() {
    return new Worker(
      new URL("../node_modules/monaco-editor/esm/vs/editor/editor.worker.js", import.meta.url),
      { type: "module" }
    );
  },
};

loader.config({ monaco });
