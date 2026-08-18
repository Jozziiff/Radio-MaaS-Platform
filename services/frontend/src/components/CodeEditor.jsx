// CodeEditor (M6.5): a real code editor for macro source, replacing the
// plain <textarea> MacroForm used to submit Python source as an
// unhighlighted blob of text. Thin wrapper around @monaco-editor/react's
// <Editor> -- value/onChange are the only props callers need, same
// data flow the textarea already had (source_code in, updated value out),
// so this is a visual swap, not a change to how the form submits.
//
// A custom theme ("signal-dark"), not Monaco's stock "vs-dark" left as-is:
// vs-dark's background/accent don't match this app's actual palette
// (index.css's signal-* scale and amber-500 accent), and dropping an
// unrelated dark theme next to the app's own dark theme would look like
// two different apps stitched together. Registered via beforeMount so
// it's defined before Monaco's first paint, avoiding a flash of the
// default theme.

import Editor from "@monaco-editor/react";

const THEME_NAME = "signal-dark";

function defineSignalDarkTheme(monaco) {
  monaco.editor.defineTheme(THEME_NAME, {
    base: "vs-dark",
    inherit: true,
    rules: [
      { token: "comment", foreground: "6b7fa3", fontStyle: "italic" },
      { token: "keyword", foreground: "f5a524" },
      { token: "keyword.control", foreground: "f5a524" },
      { token: "string", foreground: "34d399" },
      { token: "number", foreground: "ffb84d" },
      { token: "type", foreground: "c3cee0" },
      { token: "identifier", foreground: "e4e9f2" },
      { token: "delimiter", foreground: "6b7fa3" },
    ],
    colors: {
      "editor.background": "#131c2e",
      "editor.foreground": "#e4e9f2",
      "editor.lineHighlightBackground": "#1c283d80",
      "editor.selectionBackground": "#2a3a5680",
      "editorCursor.foreground": "#f5a524",
      "editorLineNumber.foreground": "#6b7fa3",
      "editorLineNumber.activeForeground": "#c3cee0",
      "editorIndentGuide.background": "#2a3a56",
      "editorIndentGuide.activeBackground": "#2a3a56",
      "editorWidget.background": "#0b1220",
      "editorWidget.border": "#1c283d",
      "editorSuggestWidget.background": "#0b1220",
      "editorSuggestWidget.border": "#1c283d",
      "editorSuggestWidget.selectedBackground": "#1c283d",
      "scrollbarSlider.background": "#2a3a5680",
      "scrollbarSlider.hoverBackground": "#2a3a56b3",
    },
  });
}

export default function CodeEditor({
  value,
  onChange,
  language = "python",
  minHeight = "20rem",
  placeholder,
}) {
  return (
    <div
      className="overflow-hidden rounded-lg border border-signal-600 focus-within:border-amber-500 focus-within:ring-1 focus-within:ring-amber-500"
      style={{ height: minHeight }}
    >
      <Editor
        language={language}
        theme={THEME_NAME}
        value={value}
        onChange={(newValue) => onChange(newValue ?? "")}
        beforeMount={defineSignalDarkTheme}
        options={{
          fontFamily: "'JetBrains Mono', ui-monospace, 'SFMono-Regular', monospace",
          fontSize: 13,
          lineNumbers: "on",
          minimap: { enabled: false },
          scrollBeyondLastLine: false,
          renderLineHighlight: "line",
          padding: { top: 12, bottom: 12 },
          automaticLayout: true,
          tabSize: 4,
          placeholder,
        }}
      />
    </div>
  );
}
