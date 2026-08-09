# Future Architecture Handoff

## Goal

Evolve `musescore-manager` into a publishing and catalog system for scores and
their derived artifacts. The system should make it easy to:

- Publish new scores.
- Generate multiple variants, such as transpositions and output formats.
- Avoid regenerating artifacts that are still valid.
- Browse published scores through a web UI.
- Keep maintenance and publication state explicit and queryable.

## Recommended Boundary

Keep this repository as a monorepo, but separate the responsibilities
architecturally and operationally:

```text
Local scores -> publisher/exporter -> catalog database
                                      |
                                      v
                               object storage
                                      |
                                web API/UI
```

The existing CLI remains the publisher. The web application is a separate
deployable component, even if it lives in this repository. The web service
should read catalog state and serve or link to artifacts; it should not invoke
MuseScore during HTTP requests.

Suggested future layout:

```text
src/msm/              # existing publishing library and CLI
src/msm/catalog/      # catalog models, migrations, and database access
src/msm/publisher/    # publication orchestration
web/                  # separate web API and UI
migrations/           # catalog schema migrations
tests/
```

Do not split into separate repositories until deployment ownership, team
boundaries, or release cadence make that worthwhile. Use clear package/API
boundaries first.

## Data Model

Keep logical works, source revisions, rendering choices, and generated files
as separate concepts:

- **Score**: A logical musical work with a stable ID and descriptive metadata.
- **Source revision**: One exact `.mscz` input, identified by content hash.
- **Variant**: Declarative rendering settings, such as key, format, page size,
  or quality.
- **Artifact**: A generated file for one source revision and one variant.
- **Publication**: Whether a score or artifact is visible in the web catalog.
- **Storage object**: The object-storage key, URL, size, hash, and provider
  details for an artifact.

Example variant:

```json
{
  "format": "png",
  "key": "Eb",
  "renderer_version": "4.6",
  "settings": {}
}
```

Artifact validity should be based on the source hash, normalized variant
settings, and renderer version, rather than only on file timestamps.

## Database Decision

Use SQLite for the catalog initially if publication is performed by one
publisher and the web service is primarily read-only. Store metadata and
publication state in SQLite, not the score or image binaries. Develop against
ordinary SQLite-compatible SQL so the catalog can run locally or on a managed
SQLite service without changing its domain model.

Turso is a suitable hosted option for this initial catalog. It provides
managed SQLite-compatible databases, Python clients, backups, branching, and
read-only access tokens. Prefer direct remote access from the web service
initially. Embedded replicas should only be introduced when persistent local
storage, background synchronization, and the resulting replica staleness are
acceptable.

For initial production conservatism, use Turso's mature libSQL access path
unless concurrent writes are a firm requirement and the newer Turso database
engine's preview status is acceptable. Keep database access behind the catalog
package so switching between local SQLite, Turso, and PostgreSQL remains a
deployment decision rather than a web or publisher concern.

Store artifacts in object storage or a managed filesystem. The database should
contain stable object keys, hashes, dimensions/page counts where useful, and
publication metadata.

Move to PostgreSQL when measured requirements justify it: substantial
multi-writer coordination, complex administrative workflows, reporting needs,
or database features and tooling that SQLite-compatible services cannot provide.
Background workers alone are not a reason to migrate.

The database is a catalog and coordination layer, not the source of truth for
whether a remote object exists. Publication should verify storage state and
support reconciliation. A database transaction cannot atomically commit an
object-storage upload and a catalog update, so publication must use explicit
retryable states rather than implying a cross-system transaction.

### Turso Operational Notes

- Use a schema-version table instead of writing `PRAGMA user_version`, which is
  read-only on Turso Cloud.
- Do not depend on changing `journal_mode`, `busy_timeout`, or
  `application_id`; Turso manages or restricts these pragmas.
- Use separate credentials for the publisher and web service. The web service
  should have a read-only database token, and the publisher should have the
  minimum required write and migration permissions.
- Index the catalog's browsing and publication queries. Turso usage is metered
  by rows read, rows written, storage, and sync bandwidth.
- Treat remote reads as network operations and define retry, timeout, and
  failure behavior. If embedded replicas are used later, document that other
  instances may not see a publication until synchronization completes.
- Test the actual schema and queries against the selected Turso engine. Turso
  is SQLite-compatible but has documented behavioral differences and separate
  `libSQL` and Turso client paths.

Turso references:

- [Turso Cloud](https://docs.turso.tech/turso-cloud)
- [Python quickstart](https://docs.turso.tech/sdk/python/quickstart)
- [Python reference](https://docs.turso.tech/sdk/python/reference)
- [Cloud limitations](https://docs.turso.tech/cloud/limitations)
- [Pricing](https://turso.tech/pricing)
- [Point-in-time recovery](https://docs.turso.tech/features/point-in-time-recovery)

## Publishing Flow

The future publishing workflow should be:

1. Scan or import a source `.mscz` file.
2. Compute its content hash and extract embedded metadata.
3. Register a new source revision only when the content changed.
4. Resolve the configured variants for that score.
5. Reuse artifacts whose source hash, variant settings, and renderer version
   still match.
6. Generate missing or stale artifacts.
7. Upload artifacts to object storage.
8. Record the upload result and object metadata in a retryable catalog state.
9. Verify the object and catalog state through reconciliation.
10. Mark selected scores or artifacts as published only after verification.

Publishing a score and generating an artifact are separate operations. This
allows drafts, retries, unpublished variants, and explicit release control.
Publication state should make partial failures visible, for example:
`pending`, `generated`, `uploading`, `uploaded`, `published`, and `failed`.
Each upload and state transition should be idempotent so a retry cannot create
an ambiguous artifact or publish an unverified object.

## Initial Implementation Stages

### Stage 1: Preserve Current Behavior

- Keep filesystem freshness checks and remote metadata synchronization.
- Avoid introducing SQLite solely as a replacement for timestamp checks.
- Define stable content-hash and variant representations before adding tables.

### Stage 2: Add a Catalog Package

- Add SQLite-backed catalog models and migrations.
- Use a schema-version table rather than `PRAGMA user_version` so the schema
  remains portable to Turso Cloud.
- Register scores and source revisions during existing CLI operations.
- Record variant definitions and artifact generation results.
- Keep the current filesystem and remote checks as reconciliation safeguards.
- Add a database adapter boundary and test the catalog against local SQLite and
  the selected Turso access path.

### Stage 3: Add Explicit Publication

- Add commands to inspect, generate, publish, and reconcile catalog entries.
- Make artifact object keys deterministic and content-addressable where
  practical.
- Record failures and renderer versions so stale outputs are explainable.
- Make upload and catalog transitions retryable and idempotent; do not treat
  object storage and the database as one atomic transaction.

### Stage 4: Add the Web Service

- Expose read-only catalog endpoints first.
- Serve artifact URLs directly from object storage or through a controlled
  download endpoint.
- Add browsing, score detail, variant selection, and download behavior.
- Keep administrative publishing outside the web request path initially.

### Stage 5: Add Background or Multi-user Workflows If Needed

- Introduce a job queue and worker process for expensive exports.
- Continue using SQLite or Turso while their measured concurrency and query
  behavior meet requirements.
- Move from SQLite or Turso to PostgreSQL only when multi-writer coordination,
  reporting, administration, or required database features justify it.
- Add authenticated administrative operations after the read-only catalog is
  stable.

## Important Constraints

- Never make HTTP requests synchronously run MuseScore exports.
- Treat source files as immutable revisions once registered.
- Make variant settings serializable and comparable.
- Keep generated artifacts reproducible by recording renderer/version settings.
- Make storage and catalog updates retryable and reconcilable.
- Define read-after-write expectations for the web service. Direct Turso access
  should read from the primary for publication-sensitive operations; embedded
  replicas require an explicit synchronization and staleness policy.
- Do not expose private storage credentials or configuration through the web
  service.

## First Concrete Design Task

Before implementing the web UI, define the catalog schema and the canonical
variant representation. Then implement a small SQLite-compatible catalog that
can run locally and against the selected Turso deployment, and can answer:

- Which scores exist?
- Which source revision is current?
- Which variants are requested?
- Which artifacts are valid, missing, failed, or published?
- Where is each published artifact stored?

Validate the design with a publication failure matrix covering upload failure,
database failure, duplicate retry, missing remote object, and stale catalog
state. The schema is the key boundary between the current publisher and the
future web application; the object-storage reconciliation process is the
reliability boundary between publication and serving.
