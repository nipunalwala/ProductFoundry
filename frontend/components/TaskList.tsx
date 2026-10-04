import type { TasksExport } from "@/lib/exports";

const KIND_NAMES: Record<string, string> = {
  happy_path: "Happy path",
  edge_case: "Edge case",
  failure_state: "Failure",
};

/** Every task in build order, with what it serves and its acceptance criteria. */
export function TaskList({ tasks }: { tasks: TasksExport }) {
  const byId = Object.fromEntries(
    tasks.epics.flatMap((epic) => epic.tasks.map((task) => [task.id, { task, epic }])),
  );
  return (
    <section aria-label="Tasks">
      <h2>Tasks</h2>
      <p>
        {tasks.build_order.length} tasks in {tasks.epics.length} epics, in the order to build
        them.
      </p>
      <ol className="cards">
        {tasks.build_order.map((id) => {
          const { task, epic } = byId[id];
          return (
            <li key={id} className="card">
              <h3>{task.title}</h3>
              <p className="facts">
                {epic.title} · effort {task.effort}
                {task.depends_on.length > 0
                  ? ` · after ${task.depends_on.map((other) => byId[other]?.task.title ?? other).join("; ")}`
                  : ""}
              </p>
              <p>{task.description}</p>
              <p className="muted">Serves:</p>
              <ul>
                {task.requirement_ids.map((requirement) => (
                  <li key={requirement}>{tasks.requirements[requirement] ?? requirement}</li>
                ))}
              </ul>
              {task.acceptance_criteria && task.acceptance_criteria.length > 0 ? (
                <>
                  <p className="muted">Acceptance criteria:</p>
                  <ul className="criteria">
                    {task.acceptance_criteria.map((criterion, index) => (
                      <li key={index}>
                        <span className="tag">{KIND_NAMES[criterion.kind] ?? criterion.kind}</span>{" "}
                        <strong>Given</strong> {criterion.given}, <strong>when</strong>{" "}
                        {criterion.when}, <strong>then</strong> {criterion.then}
                      </li>
                    ))}
                  </ul>
                </>
              ) : null}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
