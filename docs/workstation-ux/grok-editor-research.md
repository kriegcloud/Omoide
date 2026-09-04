# React image-editor library research (Gwenview-style, Pillow-backed)

**Date:** 2026-09-04
**Target app:** self-hosted photo app — React 18, MUI 7, Vite, TypeScript
**Must-have UI:** crop with draggable handles + aspect presets (16:9, 4:3, 3:2, 1:1, 9:16, 4:5, free, “current image”), rotate L/R, mirror/flip, resize, brightness/contrast/saturation, undo + reset, zoom fit/fill/percent
**Nice-to-have:** annotate. Bonus: red-eye
**Hard constraints:** npm-installable, ideally one package, MIT/Apache/BSD (no commercial-only), maintained in 2025–2026, usable as a controlled React component, **edit operations as data** (crop rect, rotation, flip, resize dims, adjustments) so the server can apply them with Pillow instead of accepting a re-encoded canvas

Stats below are live as of 2026-09-04 (npm registry + GitHub API + Bundlephobia). Weekly downloads are trailing-7-day snapshots and move.

---

## Verdict

**Top pick: `react-filerobot-image-editor@4.9.1`** (Scaleflex FIE). It is the only mature, MIT, one-package React editor that (a) covers the Gwenview feature list including annotate, (b) is still maintained in 2025–2026, and (c) already serializes the operations we need as `designState` rather than only a canvas.

**Pin 4.9.1.** npm `latest` is `5.0.0-beta.159` (June 2026) and declares `react` / `react-dom` / `react-konva` **≥19**. The app is React 18. 4.9.1 peers `react>=17` and works with `react-konva@18`.

**Runner-up: compose `react-easy-crop` + MUI sliders/history**, with `react-advanced-cropper` as the crop engine if flip/rotate-in-cropper matters more than download volume.

Do **not** start from TUI Image Editor (archived 2026-09-02), `react-cropper` (npm-stale 2023, Cropper.js v1 only), Pintura, or IMG.LY/PhotoEditor SDK.

Red-eye is not available in any evaluated OSS package. Treat it as out of scope or a later Pillow/OpenCV pass.

---

## Ranked recommendation

| Rank | Package | Role |
|---|---|---|
| **1** | `react-filerobot-image-editor@4.9.1` | Only one-package OSS editor that matches Gwenview + serializable ops |
| **2** | `react-easy-crop` (+ own MUI BCS/undo/resize) | Healthiest crop primitive; operations-as-data is trivial; you build the rest |
| **2b** | `react-advanced-cropper` | Better cropper if you need rotate+flip inside the crop UI; npm publish is stale |
| **3** | `@jodit/image-editor` | Best *model* (immutable `EditorState`, ~21 KB gz) but not a React component and tiny adoption |
| **Pass** | Cropper.js v2 + `cropperjs-react-wrapper` | Excellent crop/rotate/flip data; no BCS/annotate/undo UI |
| **Pass** | `react-image-crop` | Crop-only (very healthy) |
| **Pass** | `react-photo-editor` | BCS+rotate+draw, **no crop** |
| **Avoid** | `tui-image-editor` | Archived 2026-09-02; canvas-out; Fabric 4 |
| **Avoid** | `react-cropper` | Last npm publish 2023-04-12; Cropper.js **v1** |
| **Note only** | Pintura (pqina) | Commercial, from €169/yr; gold-standard `imageState` |
| **Note only** | IMG.LY CE.SDK / PhotoEditor SDK | Commercial; billable exports; `getOperationsStack()` exists on PE.SDK |
| **Too young** | `@dkluge/image-editor`, `@ascentsparksoftware/react-image-editor`, `@ozdemircibaris/react-image-editor` | Feature lists look right; 43–532 weekly downloads, 1–26 stars |

---

## Feature matrix (OSS, React-relevant)

Legend: **Y** = first-class, **P** = possible with config/DIY, **N** = no, **C** = commercial.

| Capability | Filerobot 4.9.1 | TUI 3.15.3 | Cropper.js v2 | react-cropper | react-image-crop | react-easy-crop | react-advanced-cropper | react-photo-editor | Jodit IE | Pintura | IMG.LY |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Crop + handles | Y | Y | Y | Y | Y | Y | Y | N | Y | Y | Y |
| Aspect presets | Y (extend) | P | P | P | P | P (`aspect`) | P | N | P | Y | Y |
| Original / “current” | Y (`original`) | P | P | P | P | P | P | N | P | Y | Y |
| Free crop | Y (`custom`) | Y | Y | Y | Y | N (fixed aspect unless you change it) | Y | N | Y | Y | Y |
| Rotate L/R | Y | Y | Y (`$rotate`) | Y (v1) | N | Y (prop) | Y | Y | Y | Y | Y |
| Flip / mirror | Y | Y | Y (`$scale(-1,1)`) | Y (v1) | N | N | Y (`flipImage`) | Y | Y | Y | Y |
| Resize | Y | P | N | N | N | N | P | N | Y | Y | Y |
| Brightness/contrast/sat | Y (Finetune) | Y (filters) | N | N | N | N | N | Y | Y | Y | Y |
| Undo / reset | Y | Y | N | N | N | N (DIY state) | N (DIY) | N | Y | Y | Y |
| Zoom fit / 100% | Y (`fitSize`, `actualSize`) | P | Y (`initialFit`) | P | N | Y (zoom prop) | Y | Y (pan/zoom) | P | Y | Y |
| Zoom fill / percent | P (presets menu) | P | P | P | N | P | P | P | P | Y | Y |
| Annotate | Y (pen/text/shapes) | Y (draw/text/shape) | N | N | N | N | N | Y (draw only) | Y (text) | Y | Y |
| Red-eye | N | N | N | N | N | N | N | N | N | N | P? |
| Ops as data (not just canvas) | **Y (`designState`)** | N (`toDataURL`) | **Y** (selection + transform) | Y (v1 `getData`) | **Y** (pixel/percent crop) | **Y** (`croppedAreaPixels`) | **Y** (`getCoordinates`) | N (File/canvas) | **Y** (`EditorState`) | **Y (`imageState`)** | **Y** (ops stack) |
| Controlled React component | Y | wrapper, stale | wrapper | Y | Y | Y | Y | Y | N (vanilla) | Y | Y |
| React 18 | Y (pin 4.9.1 + react-konva 18) | wrapper peers React ^17 | Y (wrapper) | Y | Y | Y | Y | Y | n/a | Y | Y |
| License | MIT | MIT | MIT | MIT | ISC | MIT | MIT | MIT | MIT | Commercial | Commercial |
| Maintained 2025–26 | Y (v5 beta 2026-06; v4.9.1 2024-12; repo 2026-09) | **No** (archived 2026-09-02) | Y (v2.2.0 2026-08-23) | **No** (npm 2023) | Y (11.1.2 2026-06-21) | Y (6.2.3 2026-07-24) | mixed (GitHub 2026-07, npm ~2024) | Y (3.0.0 2025-04) | Y (0.2.5 2026-07-03) | Y | Y |

---

## 1. `react-filerobot-image-editor` (Scaleflex) — **top pick**

| Field | Value |
|---|---|
| npm | [`react-filerobot-image-editor`](https://www.npmjs.com/package/react-filerobot-image-editor) |
| repo | [scaleflex/filerobot-image-editor](https://github.com/scaleflex/filerobot-image-editor) |
| Latest tag | `5.0.0-beta.159` (npm `latest`, ~2026-06-02) |
| **Use this** | **`4.9.1`** (2024-12-30) |
| Weekly downloads | **42,199** |
| License | MIT |
| Stars | 1,912 |
| Last GitHub push | 2026-06-16; repo `updated_at` 2026-09-03 |
| Bundle | **~221 KB gzip / 775 KB min** JS ([Bundlephobia](https://bundlephobia.com/package/react-filerobot-image-editor)) |
| React 18 | Yes, **4.9.1** + `react-konva@18.x` + `styled-components>=5.3.5` |
| React 19 | v5 beta peers `react>=19` / `react-konva>=19`. Do not take `latest` on this app. |

### Why it wins

It is a full editor UI in one React component, not a crop primitive:

- Tabs: Adjust (crop / rotate / flip), Finetune (brightness, contrast, HSV/saturation, warmth, blur, …), Filters, Annotate (rect, ellipse, polygon, text, line, arrow, pen, image), Resize, Watermark.
- Built-in crop presets: **Original** (“current image”), **Custom** (free), **Landscape 16:9**, **Portrait 9:16**, Ellipse. Add 4:3, 3:2, 1:1, 4:5 via `Crop.presetsItems` (first-class API).
- Undo / redo / reset and history.
- Zoom: `useZoomPresetsMenu`, translations for **Fit size** and **Actual size (100%)**. Percent zoom is there as a menu; “fill” is not a named Gwenview twin but is approximable.
- Controlled: `loadableDesignState`, `onModify(designState)`, `getCurrentImgDataFnRef`.
- **Operations object, not just pixels.** `onSave(editedImageObject, designState)` and `onModify` both give `designState`. Throw away the canvas/base64; POST the state; replay in Pillow.

`imageDesignState` (from the package’s `index.d.ts`):

```ts
{
  finetunes?: string[];
  finetunesProps?: {
    brightness?: number;
    contrast?: number;
    hue?: number;
    saturation?: number;
    value?: number;
    blurRadius?: number;
    warmth?: number;
  };
  filter?: string;
  adjustments?: {
    crop: { ratio: string | number; width?: number; height?: number; x?: number; y?: number };
    isFlippedX?: boolean;
    isFlippedY?: boolean;
    rotation?: number;
  };
  annotations?: Record<string, Annotation>;
  resize?: { width?: number; height?: number };
  shownImageDimensions?: { width: number; height: number; scaledBy: number };
}
```

That is exactly the Pillow payload (crop box, rotation, flips, resize, BCS). Annotations are Konva-ish objects (`x/y/width/height/points/text/…`) — rectangles/text are replayable in Pillow; freehand pen is the painful part.

### How to get the operation list out (top pick)

```tsx
import { useRef, useState } from 'react';
import FilerobotImageEditor, { TABS, TOOLS } from 'react-filerobot-image-editor';

type DesignState = {
  adjustments?: {
    crop?: { x?: number; y?: number; width?: number; height?: number; ratio?: string | number };
    rotation?: number;
    isFlippedX?: boolean;
    isFlippedY?: boolean;
  };
  resize?: { width?: number; height?: number };
  finetunesProps?: {
    brightness?: number;
    contrast?: number;
    saturation?: number;
    hue?: number;
    warmth?: number;
    blurRadius?: number;
  };
  shownImageDimensions?: { width: number; height: number; scaledBy: number };
  annotations?: Record<string, unknown>;
};

function toPillowOps(s: DesignState) {
  const crop = s.adjustments?.crop;
  const scale = s.shownImageDimensions?.scaledBy ?? 1;
  // VERIFY against a fixture: FIE crop x/y/w/h are in displayed-image space.
  // Divide by scaledBy (or map via shownImageDimensions) before Pillow.
  return {
    crop: crop
      ? {
          x: (crop.x ?? 0) / scale,
          y: (crop.y ?? 0) / scale,
          width: (crop.width ?? 0) / scale,
          height: (crop.height ?? 0) / scale,
        }
      : null,
    rotation: s.adjustments?.rotation ?? 0,          // degrees
    flipX: !!s.adjustments?.isFlippedX,
    flipY: !!s.adjustments?.isFlippedY,
    resize: s.resize?.width && s.resize?.height
      ? { width: s.resize.width, height: s.resize.height }
      : null,
    // Konva finetune units are NOT Pillow 1.0-identity. Calibrate once
    // (typically brightness/contrast ≈ [-1, 1] → ImageEnhance factor 1+v).
    brightness: s.finetunesProps?.brightness ?? 0,
    contrast: s.finetunesProps?.contrast ?? 0,
    saturation: s.finetunesProps?.saturation ?? 0,
    annotations: s.annotations ?? {},
  };
}

export function PhotoEditor({ src, value, onChange }: {
  src: string;
  value?: DesignState;
  onChange: (ops: ReturnType<typeof toPillowOps>, raw: DesignState) => void;
}) {
  const getCurrentImgDataFnRef = useRef<((...a: unknown[]) => { designState: DesignState }) | null>(null);

  return (
    <FilerobotImageEditor
      source={src}
      loadableDesignState={value}
      getCurrentImgDataFnRef={getCurrentImgDataFnRef}
      onModify={(designState: DesignState) => onChange(toPillowOps(designState), designState)}
      onSave={(_img, designState: DesignState) => onChange(toPillowOps(designState), designState)}
      onBeforeSave={() => false} // keep the default “download canvas” from firing
      tabsIds={[TABS.ADJUST, TABS.FINETUNE, TABS.ANNOTATE, TABS.RESIZE]}
      defaultTabId={TABS.ADJUST}
      defaultToolId={TOOLS.CROP}
      Crop={{
        ratio: 'original',
        presetsItems: [
          { titleKey: 'square', descriptionKey: '1:1', ratio: 1 },
          { titleKey: 'classicTv', descriptionKey: '4:3', ratio: 4 / 3 },
          { titleKey: 'cinemascope', descriptionKey: '3:2', ratio: 3 / 2 },
          { titleKey: 'portrait45', descriptionKey: '4:5', ratio: 4 / 5 },
          // 16:9 / 9:16 / original / custom are built-in
        ],
      }}
      useZoomPresetsMenu
      disableSaveIfNoChanges
    />
  );
}

// On "Apply": POST JSON ops to the API. Do not upload _img.imageBase64.
// const { designState } = getCurrentImgDataFnRef.current?.() ?? {};
```

Server-side mapping (Pillow):

| FIE field | Pillow |
|---|---|
| `adjustments.rotation` | `Image.rotate(-deg, expand=True)` (agree on sign; Konva clockwise vs Pillow) |
| `isFlippedX` | `ImageOps.mirror` |
| `isFlippedY` | `ImageOps.flip` |
| `crop.{x,y,width,height}` | `Image.crop((x, y, x+w, y+h))` after mapping to original pixels |
| `resize.{width,height}` | `Image.resize((w, h), Image.Resampling.LANCZOS)` |
| `finetunesProps.brightness` | `ImageEnhance.Brightness(im).enhance(1 + v)` after calibration |
| `finetunesProps.contrast` | `ImageEnhance.Contrast` |
| `finetunesProps.saturation` | `ImageEnhance.Color` |
| `annotations` | optional; skip v1, or draw rect/text with `ImageDraw` |

Apply order to match FIE: flip → rotate → crop → resize → finetunes → annotations. Confirm against one golden image; FIE’s Konva pipeline may crop-before-rotate depending on tool order. Persist the **raw `designState`** as well as the normalized ops so you can reopen the editor with `loadableDesignState`.

### Costs / known issues

- **Heavy and not MUI.** Pulls Konva 9, `@scaleflex/ui`, `@scaleflex/icons`, styled-components. Visual language will not match MUI 7 without theme overrides (`theme` prop exists). `showCanvasOnly` can strip chrome if you wrap a MUI dialog around it.
- **CORS.** Remote thumbs without `Access-Control-Allow-Origin` break filters/save. Same-origin media (this app) is fine. Don’t set `noCrossOrigin`.
- **[#545](https://github.com/scaleflex/filerobot-image-editor/issues/545)** (open, 2025-08-25): reloading `designState` leaves **annotations non-editable** (e.g. rects won’t resize). Crop/rotate/finetune reload is the documented path; annotation round-trip is the weak spot. Fine if v1 annotate is “draw then flatten on Apply”, bad if annotations must stay live.
- **[#569](https://github.com/scaleflex/filerobot-image-editor/issues/569):** blurry export if `savingPixelRatio` is low. Irrelevant if we never take the canvas.
- Crop `x/y/width/height` live in **displayed** space (`shownImageDimensions.scaledBy`). Must unit-test before shipping Pillow replay.
- Finetune numeric range is Konva-filter space, not Pillow `enhance(1.0)` identity. Calibrate once with a fixture.
- No red-eye.
- 88 open issues; project is a beta-heavy v5 rewrite. 4.9.1 is the conservative React 18 pin.
- Watermark tab: disable it (`tabsIds` above).

### React 18 install sketch

```bash
npm i react-filerobot-image-editor@4.9.1 react-konva@18.2.10 konva@9 styled-components@5.3.11
```

Do not install `react-konva@19` (peers React 19.2).

---

## 2. Runner-up — compose a cropper + MUI controls

Use this if Filerobot’s Scaleflex chrome / 221 KB / Konva unit mapping is too ugly, or if you want every pixel of the UI to be MUI 7.

### 2a. `react-easy-crop` (preferred crop primitive)

| Field | Value |
|---|---|
| npm | [`react-easy-crop`](https://www.npmjs.com/package/react-easy-crop) |
| repo | [ValentinH/react-easy-crop](https://github.com/ValentinH/react-easy-crop) |
| Latest | **6.2.3** (GitHub release 2026-07-24) |
| Weekly downloads | **3,357,890** |
| License | MIT |
| Stars | 2,771 |
| Bundle | **~7.3 KB gzip / 25 KB min** |
| React | `>=16.4` (18 fine) |
| Peers | `react`, `react-dom` only |

**Ops as data (excellent):** `onCropComplete(croppedArea, croppedAreaPixels)` → `{ x, y, width, height }` in **original image pixels**. `rotation` is a controlled number (degrees). This is the cleanest Pillow crop box in the survey.

**Has:** drag crop, zoom, rotation, aspect prop (you supply 16:9 / 4:3 / 3:2 / 1:1 / 9:16 / 4:5 / free-by-swapping-aspect / “current” = `imageWidth/imageHeight`).

**Missing (you build with MUI):** flip, resize fields, BCS sliders, undo/reset stack, zoom fit/fill/percent chrome, annotate, red-eye. Crop shape is a viewport-over-image (Instagram-style), not a Gwenview-style resizable rectangle with corner handles on a static image — close enough, but not the same interaction.

**Known issues:** no flip; aspect is a single number not a preset menu; video support we don’t need.

### 2b. `react-advanced-cropper` (better Gwenview crop interaction)

| Field | Value |
|---|---|
| npm | [`react-advanced-cropper`](https://www.npmjs.com/package/react-advanced-cropper) |
| repo | [advanced-cropper/react-advanced-cropper](https://github.com/advanced-cropper/react-advanced-cropper) |
| Latest npm | **0.20.1** (npm “2 years ago”; GitHub still pushed **2026-07-25**) |
| Weekly downloads | **174,053** |
| License | MIT (source; GitHub API SPDX is `NOASSERTION` because docs/photos have separate terms) |
| Stars | 885 |
| Bundle | **~25 KB gzip / 92 KB min** |
| React | `>=16.8` |
| Status | README still says **beta, pin with `~`** |

**Ops as data:** `getCoordinates()` → `{ left, top, width, height }`; `rotateImage(90)`; `flipImage(horizontal, vertical)`. Stencil has real **draggable handles**. Aspect via `RectangleStencil` `aspectRatio` / min / max.

**Has:** crop handles, rotate, flip, zoom/auto-zoom, coordinates mode (not just canvas).

**Missing:** BCS, resize UI, undo, annotate, built-in preset strip (you add chips).

**Risk:** npm tarball is stale vs GitHub. Pin `~0.20.1` and vendor types, or wait for a 2026 publish. Still more Gwenview-like than react-easy-crop.

### 2c. Cropper.js v2 + `cropperjs-react-wrapper`

| Field | Cropper.js | `cropperjs-react-wrapper` | `react-cropper` |
|---|---|---|---|
| Latest | **2.2.0** (2026-08-23) | **1.2.0** (2026-08-26) | **2.3.3** (2023-04-12) |
| Weekly | **1,739,619** | **472** | **431,659** |
| License | MIT | MIT | MIT |
| Bundle | **~13 KB gzip** | small wrapper | **~13 KB gzip** (plus cropperjs v1) |
| React | n/a | 18 and 19 | `>=17`, but **depends on cropperjs ^1.5.13** |

v2 dropped `getData()`. Replacement: `<cropper-selection>.{x,y,width,height}` + `<cropper-image>.$getTransform()`. Rotate `$rotate('90deg')`, flip `$scale(-1, 1)`. `initialFit` in 2.2.

**Do not use `react-cropper`** for a new integration — it is Cropper.js **v1**, last published 2023. The v2 React wrapper ([trigger-xyz/cropperjs-react-wrapper](https://github.com/trigger-xyz/cropperjs-react-wrapper), 3 stars) is maintained but young. Cropper.js itself is the most-alive crop engine in the survey (pushed **2026-09-04**).

Still crop-only. No BCS, annotate, undo.

### 2d. `react-image-crop`

| Field | Value |
|---|---|
| Latest | **11.1.2** (2026-06-21) |
| Weekly | **2,494,572** |
| License | **ISC** (permissive, OK) |
| Bundle | **~4.6 KB gzip** |
| React | `>=16.13.1` |

Pixel + percent crop objects. Aspect helper. **No rotate, flip, BCS, undo, zoom, annotate.** README points at Pintura for “real” editing. Healthy, but a crop widget, not an editor.

---

## 3. `tui-image-editor` / `@toast-ui/react-image-editor` — **do not use**

| Field | `tui-image-editor` | `@toast-ui/react-image-editor` |
|---|---|---|
| Latest | **3.15.3** (~4 years ago) | **3.15.2** |
| Weekly | **36,617** | **4,989** |
| License | MIT | MIT |
| Bundle | **~188 KB gzip / 701 KB min** | wrapper + core |
| React | vanilla | peer **`react ^17.0.2`** |
| Repo | [nhn/tui.image-editor](https://github.com/nhn/tui.image-editor) **archived 2026-09-02** | |

Has crop, rotate, flip, brightness filter, drawing, undo/redo. Looks like a match on a feature checklist.

Disqualifiers:

- **Archived 2026-09-02.** Last GitHub push 2023-11-20. 289 open issues left to rot.
- Fabric **^4.2.0** (current Fabric is 7.4.0).
- Export is `toDataURL()` — **no documented operations JSON**. You would reverse-engineer Fabric object stack.
- React wrapper still peers React 17.
- Downloads persist from legacy apps; that is not maintenance.

Community forks bump Fabric to 5.x; none are a safe default.

---

## 4. Commercial (note only)

### Pintura (pqina)

- License: **commercial**, Personal from **€169/year** ex-VAT, perpetual for versions shipped during the subscription. OEM required if customers embed it. Terms updated 2025-11-13 / 2026-01-29. [Pricing](https://pqina.nl/pintura/pricing/) · [License](https://pqina.nl/pintura/license/)
- Best-in-class `imageState` (crop, rotation, flip, resize, finetune) designed to round-trip. Closest to “send ops to the server” of any product, OSS or not.
- Runs in-browser; official server path is headless Chrome, not Pillow. You would still write a Pillow mapper from `imageState`.
- Disqualified by license.

### IMG.LY PhotoEditor SDK / CE.SDK

- Commercial; trial watermarks; license key required. CE.SDK **billable export** events. [TOS](https://img.ly/tos/)
- PE.SDK React guide: React ≥18; older `photoeditorsdk@5.19.7` for ≤18.
- PE.SDK v4: `editor.getSDK().getOperationsStack()` with identifiers `orientation`, `crop`, `filter`, `border`, `sprite`. That *is* an ops list — behind a paid SDK.
- Product push in 2026 is CE.SDK, not PE.SDK. Disqualified by license.

---

## 5. 2025–2026 OSS entrants (checked, not recommended as the one package)

### `@jodit/image-editor` — interesting model, wrong shape

| Field | Value |
|---|---|
| Latest | **0.2.5** (2026-07-03) |
| Weekly | **870** |
| License | MIT |
| Bundle | **~21 KB gzip / 68 KB min** (budget 90 KB) |
| Stars | 3 |
| React | **none** — vanilla, `view = f(state)` |

Crop, resize, rotate, flip, brightness/contrast/saturation/blur/warmth, text annotations, undo/redo. Immutable `EditorState` via `editor.update(patch)` — conceptually the best OSS “ops are data” design after Filerobot’s `designState`.

Not a React component. 3 stars. Wrap it and you own the React lifecycle. Keep as a **design reference** for the Pillow DTO, not as the UI.

### `@dkluge/image-editor`

| Field | Value |
|---|---|
| Latest | 1.0.16 (2025-11-09) |
| Weekly | **134** |
| Stars | 4 |
| License | MIT |
| React | `>=18` |

README claims crop/rotate/flip/resize, 15+ filters, BCS/exposure/gamma/vignette, annotate, stickers, frames, `imageState` + canvas on confirm, `cropSelectPresetOptions` including Original / Square / 16:9 / 9:16.

Too small (4 stars, 134 weekly, last commit 2025-11). `imageState` is typed as opaque `EditorState` — not documented field-by-field like FIE. Do not bet the photo app on it.

### `@ascentsparksoftware/react-image-editor`

Fabric.js v7, React **^19 only**, MIT, v1.0.0, **43** weekly, 1 star, pushed 2026-08-04. Feature-rich (crop, straighten, BCS, draw, text, redaction, layers) but React 19 and canvas/JSON export. Pass.

### `@ozdemircibaris/react-image-editor`

1.3.2, MIT, React ≥16.8, **532** weekly, 26 stars, pushed 2026-06-21. Headless + styled. Crop, blur, shapes, draw, undo. **No rotate, no brightness** in the README. Fabric canvas. Pass.

### `react-photo-editor` (musama619)

| Field | Value |
|---|---|
| Latest | **3.0.0** (2025-04-05) |
| Weekly | **1,253** |
| Bundle | **~7 KB gzip** |
| Stars | 83 |
| React | 18.2 / 19 |
| License | MIT |

Rotate, flip, BCS, grayscale, pan/zoom, drawing. **No crop.** Canvas/`File` out. Useful as a BCS-slider reference, not the editor.

### `@ente-io/photo-editor-sdk`

GitHub [ente/photo-editor-sdk](https://github.com/ente/photo-editor-sdk): MIT-ish README, crop/rotate/filters, hooks `usePhotoTransformer` / `usePhotoColourAdjuster` (brightness, contrast, blur, saturation, invert). **Not on npm** (404). Last push 2023-11-05, 15 stars, 6 commits. Dead for our purposes.

### Fabric.js / Konva as a kit

| | `fabric` | `konva` | `react-konva` |
|---|---|---|---|
| Latest | 7.4.0 | 10.3.3 | 19.2.6 (React 19) / 18.2.10 (React 18) |
| Weekly | 959,499 | 2,835,653 | 2,235,562 |
| License | MIT | MIT | MIT |

These are canvases, not editors. Filerobot *is* the Konva editor. TUI *was* the Fabric editor. Building Gwenview on raw Konva duplicates FIE (history, crop transformer, finetune filters, annotate tools). Only justified if Filerobot’s UI is rejected and the compose path is also rejected.

`swimmingkiim/react-image-editor` (~551 stars) is a Canva-like demo, not a library.

---

## 6. Mapping the Gwenview checklist onto the top pick

| Gwenview-ish need | Filerobot 4.9.1 |
|---|---|
| Crop, draggable handles | Adjust → Crop (Konva transformer) |
| 16:9 | Built-in Landscape |
| 9:16 | Built-in Portrait |
| 4:3, 3:2, 1:1, 4:5 | `Crop.presetsItems` (one config array) |
| Free | Built-in Custom |
| Current image | Built-in Original (`ratio: 'original'`) |
| Rotate L/R | Adjust → Rotate (buttons or slider) |
| Mirror / flip | Adjust → Flip X/Y |
| Resize | Resize tab (`width`/`height` in `designState.resize`) |
| Brightness / contrast / saturation | Finetune → Brightness, Contrast, HSV |
| Undo / reset | Built-in history |
| Zoom fit | `fitSize` + `useZoomPresetsMenu` |
| Zoom 100% | `actualSize` |
| Zoom fill / percent | Preset menu; add labels if you want Gwenview parity |
| Annotate | Annotate tab (pen, text, shapes, arrows) |
| Red-eye | **Absent** everywhere OSS; skip or do server-side later |
| Controlled | `loadableDesignState` + `onModify` |
| Ops for Pillow | `designState` as above |
| One MIT package | Yes (plus react-konva / konva / styled-components peers) |

---

## 7. Risks if we pick Filerobot (decision record)

1. **Pin 4.9.1 explicitly** in `package.json`. `npm i react-filerobot-image-editor` currently installs a React-19 beta.
2. **Calibrate coordinates and finetune units** with a fixture image before wiring Pillow. Budget a half-day. Persist raw `designState` alongside normalized ops.
3. **Annotations after reload** ([#545](https://github.com/scaleflex/filerobot-image-editor/issues/545)). If live annotation editing on reopen matters, either flatten annotations on Apply (Pillow draws them once) or don’t advertise annotate in v1.
4. **Look and feel.** Scaleflex + styled-components inside an MUI 7 shell. Theme via `theme` / `translations`, or `showCanvasOnly` + our chrome. If visual unity is non-negotiable, take the runner-up compose path.
5. **Bundle.** ~221 KB gz vs ~7 KB for react-easy-crop. Acceptable for a route-level editor (`React.lazy`).
6. No red-eye. Don’t promise it.

---

## 8. Suggested decision

- **Ship Filerobot 4.9.1** as the editor surface. It is the only OSS package that is simultaneously: one install, Gwenview-complete, MIT, React-18-capable, maintained through 2026, and already emitting a Pillow-shaped operations object.
- **Pillow applies `designState`**, never a browser JPEG. Keep original bytes; store ops JSON next to the asset; reopen with `loadableDesignState`.
- **If** theme clash or Konva-unit mapping explodes in the first spike, **fall back to runner-up**: `react-easy-crop` (or `react-advanced-cropper` for handle-style crop + flip) + MUI sliders for BCS + a small undo stack you own. That path is more work and loses annotate, but ops-as-data becomes trivial and the UI is native MUI.
- **Do not** adopt TUI, `react-cropper`, Pintura, or IMG.LY for this app.

---

## Sources

- npm registry / downloads API (2026-09-04): `react-filerobot-image-editor`, `tui-image-editor`, `cropperjs`, `react-cropper`, `react-image-crop`, `react-easy-crop`, `react-advanced-cropper`, `react-photo-editor`, `cropperjs-react-wrapper`, `@toast-ui/react-image-editor`, `@jodit/image-editor`, `@dkluge/image-editor`, `@ascentsparksoftware/react-image-editor`, `@ozdemircibaris/react-image-editor`, `fabric`, `konva`, `react-konva`
- GitHub API: [scaleflex/filerobot-image-editor](https://github.com/scaleflex/filerobot-image-editor), [nhn/tui.image-editor](https://github.com/nhn/tui.image-editor), [fengyuanchen/cropperjs](https://github.com/fengyuanchen/cropperjs), [ValentinH/react-easy-crop](https://github.com/ValentinH/react-easy-crop), [dominictobias/react-image-crop](https://github.com/dominictobias/react-image-crop), [advanced-cropper/react-advanced-cropper](https://github.com/advanced-cropper/react-advanced-cropper), [react-cropper/react-cropper](https://github.com/react-cropper/react-cropper), [trigger-xyz/cropperjs-react-wrapper](https://github.com/trigger-xyz/cropperjs-react-wrapper), [jodit/jodit-image-editor](https://github.com/jodit/jodit-image-editor), [dkluge-design/dk-image-editor](https://github.com/dkluge-design/dk-image-editor), [musama619/react-photo-editor](https://github.com/musama619/react-photo-editor), [ente/photo-editor-sdk](https://github.com/ente/photo-editor-sdk)
- FIE types: `packages/react-filerobot-image-editor/src/index.d.ts`; crop presets: `.../tools/Crop/Crop.constants.js`; defaults: `.../context/defaultConfig.js`
- Cropper.js v2: [API](https://fengyuanchen.github.io/cropperjs/v2/api/), [migration](https://fengyuanchen.github.io/cropperjs/v2/migration.html)
- TUI: [ImageEditor API](https://nhn.github.io/tui.image-editor/latest/ImageEditor/)
- Bundlephobia: filerobot, tui-image-editor, cropperjs, react-easy-crop, react-image-crop, react-advanced-cropper, react-cropper, react-photo-editor, @jodit/image-editor
- Pintura: [pricing](https://pqina.nl/pintura/pricing/), [imageState restore](https://pqina.nl/pintura/docs/v8/examples/restore-image-state/)
- IMG.LY: [PE.SDK operations stack](https://img.ly/docs/pesdk/web/v4/concepts/events/), [TOS](https://img.ly/tos/)
- Issues: FIE [#545](https://github.com/scaleflex/filerobot-image-editor/issues/545), [#569](https://github.com/scaleflex/filerobot-image-editor/issues/569), [#319](https://github.com/scaleflex/filerobot-image-editor/issues/319)
