import { installPresentationGuard } from "./presentationGuard";
import { loadReactGrab } from "./devtools/reactGrab";

installPresentationGuard();

// React Grab must install its DevTools hook before react-dom evaluates, so the
// app (and React itself) is imported only after it has loaded or been skipped.
void loadReactGrab().finally(() => import("./bootstrap"));
