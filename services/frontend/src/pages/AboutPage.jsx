// AboutPage (M7): a simple, static "what is this / who built it" page --
// visible to every session (not admin-gated, unlike AdminPage), reachable
// via Sidebar's About nav item. No live data, no API calls -- just the
// platform description and a credit section, styled with the same
// Shell/TopBar/Card treatment as every other page so it reads as part of
// the app rather than a bolted-on static page.

import Shell from "../components/Shell";
import TopBar from "../components/TopBar";
import Card from "../components/Card";

export default function AboutPage({ page, onNavigate }) {
  return (
    <Shell page={page} onNavigate={onNavigate}>
      <TopBar title="About" description="What this platform is, and who built it." />

      <main className="mx-auto max-w-2xl px-8 pb-10 space-y-4">
        <Card className="p-8">
          <div className="flex items-center gap-3">
            <img src="/Orange-logo.png" alt="Orange" className="h-8 w-8 rounded object-cover" />
            <h2 className="font-mono text-base font-medium text-signal-100">radio-maas</h2>
          </div>
          <p className="mt-4 text-sm leading-relaxed text-signal-300">
            A Macro-as-a-Service platform built for Orange Tunisie's RADIO-OPTIM
            team. It turns manually-run Python radio-analysis scripts ("macros")
            into on-demand, containerized services, with a web interface for
            building, running, and tracking them against CSV data — no manual
            Dockerfile writing, no manual deployment.
          </p>
        </Card>

        <Card className="p-8">
          <h2 className="text-sm font-medium text-signal-100">Built by</h2>
          <div className="mt-4 flex items-center gap-4">
            <img
              src="/picture.jpg"
              alt="Youssef Hamdani"
              className="h-16 w-16 shrink-0 rounded-full object-cover object-[50%_20%] ring-2 ring-signal-700"
            />
            <div>
              <p className="text-sm font-medium text-signal-100">Youssef Hamdani</p>
              <p className="mt-0.5 text-sm text-signal-400">
                Réseaux et Télécommunications engineering student, INSAT
              </p>
              <p className="mt-0.5 text-sm text-signal-400">
                Developed as a summer internship project at Orange Tunisie (2026)
              </p>
            </div>
          </div>
        </Card>
      </main>
    </Shell>
  );
}
