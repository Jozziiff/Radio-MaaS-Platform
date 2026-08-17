// TopBar (M6, design pass): the slim per-page title bar that sits above a
// page's content, inside Shell. Carries the page title/description on the
// left and a single primary action on the right (e.g. Catalog's "New
// macro" button) -- content-area concerns, kept separate from Sidebar's
// app-level navigation.

export default function TopBar({ title, description, action }) {
  return (
    <div className="mb-6 flex items-center justify-between border-b border-signal-700 px-8 py-5">
      <div>
        <h1 className="text-lg font-medium text-signal-100">{title}</h1>
        {description && <p className="mt-1 text-sm text-signal-400">{description}</p>}
      </div>
      {action}
    </div>
  );
}
