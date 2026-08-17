// AmbientGlow (M6, night-glow pass): three large, blurred, low-opacity
// radial gradients that drift slowly behind page content -- the "quietly
// alive" background ambience the dark theme is built around. Pure CSS
// (positioning + the drift keyframes live in index.css), no JS animation
// loop, so it costs nothing at runtime beyond three composited layers.
//
// Fixed positioning + a negative z-index-equivalent (z-0 with content
// wrapped at z-10 by each caller) keeps this strictly behind everything,
// including on scrollable pages -- it never competes with text for
// attention, per the brief's explicit "never behind text in a way that
// hurts readability" constraint. pointer-events-none so it can never
// intercept a click meant for real content.
//
// Three colors, not the brand's single amber alone: signal-blue and a
// deep violet echo the "control-room" palette's cooler end (index.css's
// signal-* scale), amber echoes the brand accent already used for every
// CTA -- so the ambience reads as "this app's palette, dimmed" rather
// than a generic unrelated gradient.

export default function AmbientGlow() {
  return (
    <div aria-hidden="true" className="pointer-events-none fixed inset-0 z-0 overflow-hidden">
      <div className="glow-blob glow-blob-a" />
      <div className="glow-blob glow-blob-b" />
      <div className="glow-blob glow-blob-c" />
    </div>
  );
}
