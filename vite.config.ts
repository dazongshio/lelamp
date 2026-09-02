import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    sourcemap: false,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes("node_modules/docx/")) return "document-export-vendor";
          if (
            id.includes("node_modules/@tiptap/pm/")
            || id.includes("node_modules/prosemirror-")
          ) return "document-editor-prosemirror";
          if (
            id.includes("node_modules/@hocuspocus/")
            || id.includes("node_modules/yjs/")
            || id.includes("node_modules/y-prosemirror/")
            || id.includes("node_modules/y-protocols/")
            || id.includes("node_modules/lib0/")
          ) return "document-editor-collaboration";
          if (id.includes("node_modules/@tiptap/")) return "document-editor-tiptap";
          return undefined;
        },
      },
    },
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8790",
        changeOrigin: true,
      },
    },
  },
});
