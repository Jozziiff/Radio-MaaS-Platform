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

      <main className="mx-auto max-w-3xl px-8 pb-16 space-y-6">
        <Card className="p-10">
          <div className="flex items-center gap-4">
            <img src="/Orange-logo.png" alt="Orange" className="h-14 w-14 object-contain" />
            <h2 className="font-mono text-xl font-medium text-signal-100">radio-maas</h2>
          </div>
          <p className="mt-6 text-base leading-relaxed text-signal-200">
            A Macro-as-a-Service platform built for Orange Tunisie's RADIO-OPTIM
            team. It turns manually-run Python radio-analysis scripts ("macros")
            into on-demand, containerized services, with a web interface for
            building, running, and tracking them against CSV data — no manual
            Dockerfile writing, no manual deployment.
          </p>
        </Card>

        <Card className="p-10">
          <h2 className="text-base font-medium text-signal-100">Built by</h2>
          <div className="mt-6 flex items-center gap-6">
            <img
              src="/picture.jpg"
              alt="Youssef Hamdani"
              className="h-24 w-24 shrink-0 rounded-full object-cover object-[50%_20%] ring-2 ring-signal-700"
            />
            <div>
              <p className="text-base font-medium text-signal-100">Youssef Hamdani</p>
              <p className="mt-1 text-sm text-signal-200">
                Réseaux et Télécommunications engineering student, INSAT
              </p>
              <p className="mt-1 text-sm text-signal-200">
                Developed as a summer internship project at Orange Tunisie (2026)
              </p>
            </div>
          </div>
        </Card>
      </main>
    </Shell>
  );
}
