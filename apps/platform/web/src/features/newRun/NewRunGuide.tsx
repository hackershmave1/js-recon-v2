// The side panel beside the New recon form: what the chosen mode does and what each
// option on the form means, in plain words. It only explains options the form actually
// shows (runtime capture is listed only when the server offers it), so the two never
// disagree. Static copy only: no counts or run data here.
export function NewRunGuide({ mode, captureAvailable }: {
  mode: "crawl" | "upload";
  captureAvailable: boolean;
}) {
  return (
    <aside className="card nr-guide" aria-labelledby="nr-guide-title">
      <h3 id="nr-guide-title" className="nr-guide-title">
        {mode === "crawl" ? "What a crawl does" : "What an upload does"}
      </h3>
      {mode === "crawl" ? (
        <p className="nr-guide-lead">
          Loads the domain&apos;s pages, collects every JavaScript file they serve (plus source maps
          and lazy-loaded chunks), and reads that code to rebuild the API it talks to. It only
          fetches from hosts in scope and sends no attack traffic.
        </p>
      ) : (
        <p className="nr-guide-lead">
          Reads one JavaScript file you already have and rebuilds the API it talks to. Nothing is
          fetched from the network.
        </p>
      )}

      <dl className="nr-guide-list">
        {mode === "crawl" && captureAvailable && (
          <>
            <dt>Runtime capture</dt>
            <dd>Also opens the site in a headless browser to catch code that only appears while it runs. Slower; beta.</dd>
          </>
        )}
        <dt>Scope hosts</dt>
        <dd>
          {mode === "crawl"
            ? "The hosts the crawl may fetch from. Leave empty to use the domain and its subdomains; add a CDN or API host to widen it."
            : "Optional for an upload, since nothing is fetched."}
        </dd>
        <dt>Broader secret scan</dt>
        <dd>Finds more possible secrets, but about half are false alarms. They stay in their own &quot;suspected secret&quot; list.</dd>
        <dt>Authorized by</dt>
        <dd>Who approved testing this target. Required: it is your authorization record.</dd>
      </dl>
    </aside>
  );
}
