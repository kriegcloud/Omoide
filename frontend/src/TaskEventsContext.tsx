import { mutationBus } from "./stores/mutationBus";
import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { getTask } from "./services/task";
import { getActiveTasks, getRecentTasks } from "./services/taskActions";
import { Task, TaskType } from "./types";

const INITIAL_COUNTERS: Record<TaskType, number> = {
  scan: 0,
  process_media: 0,
  clean_missing_files: 0,
  cluster_persons: 0,
  find_duplicates: 0,
  compute_blur_scores: 0,
  detect_watermarks: 0,
  run_processor: 0,
  run_processor_for_media: 0,
  auto_tag_custom: 0,
  backfill_face_timestamps: 0,
  backfill_face_quality: 0,
  backfill_demographics: 0,
  pose_backfill: 0,
  generate_hashes: 0,
  build_events: 0,
  geocode_places: 0,
  export_person_media: 0,
  convert_media: 0,
  convert_videos: 0,
  batch_edit_media: 0,
  dataset_frame_mining: 0,
  dataset_caption_generation: 0,
  export_dataset: 0,
};

const BASE_POLL_INTERVAL_MS = 2000;
const MAX_POLL_INTERVAL_MS = 30000;

type TaskEventsContextValue = {
  activeTasks: Task[];
  recentTasks: Task[];
  completionCounters: Record<TaskType, number>;
  globalCompletionCount: number;
  lastCompletedTasks: Partial<Record<TaskType, Task>>;
  lastFinishedTask: { task: Task; nonce: number } | null;
  forceRefresh: () => Promise<void>;
  subscribe: () => () => void;
};

const TaskEventsContext = createContext<TaskEventsContextValue | null>(null);
type CompletionContextValue = Pick<TaskEventsContextValue,
  "completionCounters" | "globalCompletionCount" | "subscribe">;
const TaskCompletionContext = createContext<CompletionContextValue | null>(null);
const isTerminal = (task: Task) =>
  ["completed", "failed", "cancelled", "interrupted"].includes(task.status);

async function safeFetchTask(id: string): Promise<Task | null> {
  try {
    return await getTask(id);
  } catch (error) {
    console.warn("Failed to fetch task", id, error);
    return null;
  }
}

export function TaskEventsProvider({
  children,
  onTaskFinished,
}: {
  children: React.ReactNode;
  /** Once per observed terminal task, including failures. Use this to look up an
   * operation receipt and call reconcileOperation; task status is not a receipt.
   * Historical terminal tasks on the initial snapshot are deliberately excluded.
   * Async callbacks own their retry policy; polling never replays mutations. */
  onTaskFinished?: (task: Task) => void | Promise<void>;
}) {
  const [activeTasks, setActiveTasks] = useState<Task[]>([]);
  const [recentTasks, setRecentTasks] = useState<Task[]>([]);
  const [completionCounters, setCompletionCounters] =
    useState<Record<TaskType, number>>(INITIAL_COUNTERS);
  const [globalCompletionCount, setGlobalCompletionCount] = useState(0);
  const [lastCompletedTasks, setLastCompletedTasks] =
    useState<Partial<Record<TaskType, Task>>>({});
  const [lastFinishedTask, setLastFinishedTask] = useState<{
    task: Task;
    nonce: number;
  } | null>(null);

  const finishedCallbackRef = useRef(onTaskFinished);
  finishedCallbackRef.current = onTaskFinished;
  const idleCountRef = useRef(0);
  const generationRef = useRef(0);
  const unresolvedRef = useRef(new Map<string, Task>());
  const finishedIdsRef = useRef(new Set<string>());
  const prevTasksRef = useRef<Record<string, Task>>({});
  const recentIdsRef = useRef<Set<string> | null>(null);
  const pendingFetchRef = useRef<Promise<boolean> | null>(null);
  const subscribersRef = useRef(0);
  const pollTimeoutRef = useRef<number | null>(null);
  const pollingActiveRef = useRef(false);
  const pollingCycleRef = useRef(0);
  const pausedRef = useRef(false);
  const failureCountRef = useRef(0);
  const isMountedRef = useRef(true);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      generationRef.current += 1;
      pendingFetchRef.current = null;
    };
  }, []);

  const applyFinishedTasks = useCallback(async (finished: Task[], generation: number) => {
    finished.forEach(task => {
      const known = unresolvedRef.current.get(task.id);
      if (!finishedIdsRef.current.has(task.id) && (!known || !isTerminal(known) || isTerminal(task))) {
        unresolvedRef.current.set(task.id, task);
      }
    });
    const resolved = await Promise.all(Array.from(unresolvedRef.current.values()).map(
      task => isTerminal(task) ? Promise.resolve(task) : safeFetchTask(task.id)
    ));
    if (!isMountedRef.current || generation !== generationRef.current) return;
    const resolvedTasks = resolved.filter((task): task is Task => Boolean(task && isTerminal(task)));
    resolvedTasks.forEach(task => {
      unresolvedRef.current.delete(task.id);
      finishedIdsRef.current.add(task.id);
    });
    for (const task of resolvedTasks) {
      try {
        void Promise.resolve(finishedCallbackRef.current?.(task)).catch(error => {
          console.warn("Task completion callback failed", task.id, error);
        });
      } catch (error) {
        console.warn("Task completion callback failed", task.id, error);
      }
    }
    const completedTasks = resolvedTasks.filter(task => task.status === "completed");
    if (!resolvedTasks.length) return;
    // Even failed/cancelled jobs can have checkpointed writes. Re-read rather
    // than claim an unreported successful subset from a background operation.
    mutationBus.emit({ type: "list:invalidate", prefix: "" });

    setLastFinishedTask((previous) => ({
      task: resolvedTasks[0],
      nonce: (previous?.nonce ?? 0) + 1,
    }));

    if (!completedTasks.length) return;

    const completedTypes = completedTasks.map((task) => task.task_type as TaskType);

    setLastCompletedTasks((prev) => {
      const next = { ...prev };
      completedTasks.forEach((task) => {
        next[task.task_type as TaskType] = task;
      });
      return next;
    });

    setCompletionCounters((prev) => {
      const next: Record<TaskType, number> = { ...prev };
      completedTypes.forEach((type) => {
        next[type] = (next[type] ?? 0) + 1;
      });
      return next;
    });

    setGlobalCompletionCount((value) => value + completedTypes.length);
  }, []);

  const fetchTasks = useCallback((): Promise<boolean> => {
    // All callers await the same in-flight poll. Waiting and then starting a
    // new request lets several waiting callers bypass the lock together.
    if (pendingFetchRef.current) return pendingFetchRef.current;
    const generation = generationRef.current;
    const run = (async () => {
      try {
        const [tasks, recent] = await Promise.all([
          getActiveTasks(),
          getRecentTasks(),
        ]);
        if (!isMountedRef.current || generation !== generationRef.current) return true;

        // Task payloads are JSON. Compare all fields (including nested params and
        // results), retaining references when the server snapshot is unchanged.
        setActiveTasks(previous => JSON.stringify(previous) === JSON.stringify(tasks) ? previous : tasks);
        setRecentTasks(previous => JSON.stringify(previous) === JSON.stringify(recent) ? previous : recent);

        const nextMap = tasks.reduce<Record<string, Task>>((acc, task) => {
          acc[task.id] = task;
          return acc;
        }, {});

        const prevMap = prevTasksRef.current;
        const disappeared = Object.values(prevMap).filter(
          (task) => !nextMap[task.id]
        );
        prevTasksRef.current = nextMap;

        const previousRecentIds = recentIdsRef.current;
        if (previousRecentIds === null) {
          recent.filter(isTerminal).forEach(task => finishedIdsRef.current.add(task.id));
        }
        const newlyRecent = previousRecentIds
          ? recent.filter((task) => !previousRecentIds.has(task.id))
          : [];
        recentIdsRef.current = new Set(recent.map((task) => task.id));

        // A task can enter recent history while still pending/running. Prefer
        // its latest snapshot when retrying a disappeared/nonterminal task.
        for (const task of recent) {
          if (unresolvedRef.current.has(task.id)) unresolvedRef.current.set(task.id, task);
        }
        const finished = [
          ...newlyRecent.filter(task => isTerminal(task) || !nextMap[task.id]),
          ...recent.filter(task => !nextMap[task.id] && (!isTerminal(task) || prevMap[task.id])),
          ...disappeared.filter(task => !recentIdsRef.current?.has(task.id)),
        ];
        await applyFinishedTasks(finished, generation);
        return true;
      } catch (error) {
        if (import.meta.env.DEV) {
          console.warn("Failed to poll active tasks", error);
        }
        return false;
      }
    })();

    pendingFetchRef.current = run;
    void run.finally(() => {
      if (pendingFetchRef.current === run) pendingFetchRef.current = null;
    });
    return run;
  }, [applyFinishedTasks]);

  const pollTick = useCallback(async () => {
    if (!pollingActiveRef.current) return;
    const cycle = pollingCycleRef.current;
    if (
      typeof document !== "undefined" &&
      document.visibilityState === "hidden"
    ) {
      // Pause while the tab is hidden; the visibilitychange handler resumes.
      pausedRef.current = true;
      return;
    }

    const success = await fetchTasks();
    if (cycle !== pollingCycleRef.current) return;
    failureCountRef.current = success ? 0 : Math.min(failureCountRef.current + 1, 4);
    const busy = Object.keys(prevTasksRef.current).length > 0 || unresolvedRef.current.size > 0;
    idleCountRef.current = success && !busy ? Math.min(idleCountRef.current + 1, 5) : 0;

    if (!pollingActiveRef.current || pausedRef.current) return;
    const delay = Math.min(
      BASE_POLL_INTERVAL_MS * 2 ** Math.max(failureCountRef.current, idleCountRef.current - 1),
      MAX_POLL_INTERVAL_MS
    );
    if (pollTimeoutRef.current !== null) window.clearTimeout(pollTimeoutRef.current);
    pollTimeoutRef.current = window.setTimeout(() => {
      void pollTick();
    }, delay);
  }, [fetchTasks]);

  const handleVisibilityChange = useCallback(() => {
    if (document.visibilityState !== "visible") return;
    if (!pollingActiveRef.current || !pausedRef.current) return;
    pausedRef.current = false;
    void pollTick();
  }, [pollTick]);

  const startPolling = useCallback(() => {
    if (typeof window === "undefined") return;
    if (pollingActiveRef.current) return;
    pollingActiveRef.current = true;
    pausedRef.current = false;
    failureCountRef.current = 0;
    idleCountRef.current = 0;
    document.addEventListener("visibilitychange", handleVisibilityChange);
    void pollTick();
  }, [pollTick, handleVisibilityChange]);

  const stopPolling = useCallback(() => {
    if (typeof window === "undefined") return;
    pollingActiveRef.current = false;
    pollingCycleRef.current += 1;
    pausedRef.current = false;
    document.removeEventListener("visibilitychange", handleVisibilityChange);
    if (pollTimeoutRef.current !== null) {
      window.clearTimeout(pollTimeoutRef.current);
      pollTimeoutRef.current = null;
    }
  }, [handleVisibilityChange]);

  const subscribe = useCallback(() => {
    subscribersRef.current += 1;
    if (subscribersRef.current === 1) {
      startPolling();
    }
    return () => {
      subscribersRef.current = Math.max(0, subscribersRef.current - 1);
      if (subscribersRef.current === 0) {
        stopPolling();
      }
    };
  }, [startPolling, stopPolling]);

  useEffect(() => () => {
    stopPolling();
  }, [stopPolling]);

  const forceRefresh = useCallback(async () => {
    idleCountRef.current = 0;
    await fetchTasks();
    // Explicit refresh (e.g. accepted task submission) wakes an idle scheduler.
    // Clear before replacing so concurrent callers still leave only one timer.
    if (isMountedRef.current && pollingActiveRef.current && !pausedRef.current && document.visibilityState !== "hidden") {
      if (pollTimeoutRef.current !== null) window.clearTimeout(pollTimeoutRef.current);
      pollTimeoutRef.current = window.setTimeout(() => { void pollTick(); }, BASE_POLL_INTERVAL_MS);
    }
  }, [fetchTasks, pollTick]);

  const value = useMemo(
    () => ({
      activeTasks,
      recentTasks,
      completionCounters,
      globalCompletionCount,
      lastCompletedTasks,
      lastFinishedTask,
      forceRefresh,
      subscribe,
    }),
    [
      activeTasks,
      recentTasks,
      completionCounters,
      globalCompletionCount,
      lastCompletedTasks,
      lastFinishedTask,
      forceRefresh,
      subscribe,
    ]
  );

  const completionValue = useMemo(() => ({ completionCounters, globalCompletionCount, subscribe }),
    [completionCounters, globalCompletionCount, subscribe]);

  return (
    <TaskEventsContext.Provider value={value}>
      <TaskCompletionContext.Provider value={completionValue}>
        {children}
      </TaskCompletionContext.Provider>
    </TaskEventsContext.Provider>
  );
}

export function useTaskEvents(shouldSubscribe = true) {
  const ctx = useContext(TaskEventsContext);
  if (!ctx) {
    throw new Error("useTaskEvents must be used within a TaskEventsProvider");
  }
  const { subscribe, ...rest } = ctx;
  useEffect(() => {
    if (!shouldSubscribe) {
      return undefined;
    }
    const unsubscribe = subscribe();
    return unsubscribe;
  }, [subscribe, shouldSubscribe]);
  return rest;
}

export function useTaskCompletionVersion(taskTypes?: TaskType[]) {
  const ctx = useContext(TaskCompletionContext);
  if (!ctx) throw new Error("useTaskCompletionVersion must be used within a TaskEventsProvider");
  const { completionCounters, globalCompletionCount, subscribe } = ctx;
  useEffect(() => subscribe(), [subscribe]);
  if (!taskTypes || taskTypes.length === 0) {
    return globalCompletionCount;
  }
  return taskTypes.reduce(
    (acc, type) => acc + (completionCounters[type] ?? 0),
    0
  );
}
