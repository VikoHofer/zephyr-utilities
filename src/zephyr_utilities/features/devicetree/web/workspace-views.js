// Support browsing does not open a project or start chip discovery. Leaving the
// editor uses its normal disposal path; drafts remain in the shared feature state.
export function createWorkspaceViews({editorRoot, supportRoot, mountEditor, mountSupport}) {
  let editor;
  let support;
  let disposed = false;
  return {
    navigate(view = "editor") {
      if (disposed) return false;
      const showSupport = view === "support";
      if (showSupport) {
        editor?.dispose();
        editor = null;
        support ||= mountSupport();
      }
      editorRoot.hidden = showSupport;
      supportRoot.hidden = !showSupport;
      if (showSupport) return true;
      editor ||= mountEditor();
      return editor.navigate(view);
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      editor?.dispose();
      support?.dispose();
    },
  };
}
