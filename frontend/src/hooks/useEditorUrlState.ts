import { useCallback, useRef } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { editorCloseAction, markEditorPushed, patchEditorSearch, type EditorQueryPatch } from "../utils/editorUrlState";

/** Opening/closing adds history; in-dialog changes and invalid URLs replace it. */
export function useEditorUrlState() {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const latest = useRef({ params, setParams, state: location.state });
  latest.current = { params, setParams, state: location.state };
  const patch = useCallback((changes: EditorQueryPatch, replace = true, state?: unknown) => {
    const current = latest.current;
    const next = patchEditorSearch(current.params, changes);
    if (next.toString() === current.params.toString()) return;
    // React Router does not queue functional search updates. Merge consecutive
    // changes against our latest write, and retain viewer navigation context.
    current.params = next;
    current.setParams(next, { replace, state: state === undefined ? current.state : state });
  }, []);
  const navigate = useNavigate();
  /** Open an editor (edit/crop) as one new history entry. */
  const open = useCallback((changes: EditorQueryPatch, state?: unknown) => {
    patch(changes, false, markEditorPushed(state === undefined ? latest.current.state : state));
  }, [patch]);
  /** Close it: pop the entry we pushed, or replace when opened from a link/reload. */
  const close = useCallback((changes: EditorQueryPatch = { edit: null }) => {
    if (editorCloseAction(latest.current.state) === "back") navigate(-1);
    else patch(changes, true);
  }, [navigate, patch]);
  return { params, patch, open, close };
}
