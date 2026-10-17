using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Globalization;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using UnityEngine;
#if MINESIM_UWR
using System.Collections;
using UnityEngine.Networking;
#endif

namespace MineSim
{
    /// <summary>
    /// Optional demo driver (OFF by default): runs a haul cycle for every TruckAgent by posting goals to the brain's web API,
    /// so the trucks are driven by the brain's own policy (throttle/steer over TCP) the whole time:
    ///   ore site load_point  ->  crusher dump_point  ->  repeat.
    /// It only ever calls <c>POST {baseUrl}/api/trucks/{id}/goal {"x","z"}</c> (and DELETE on disable), see spec.md "Web".
    ///
    /// Waypoints: with <see cref="followRoads"/> the leg is the road-graph route (MineRoadGraph) resampled every
    /// <see cref="waypointSpacing"/> m, ending at the load/dump point. Intermediate goals advance at
    /// <see cref="passRadius"/> (larger than the policy's 4 m arrive radius, so the truck never stops between waypoints);
    /// the final goal of a leg counts as reached at <see cref="arriveRadius"/> + <see cref="arriveSlack"/>, then the truck
    /// dwells <see cref="dwellSeconds"/> (loading / tipping) before the next leg.
    ///
    /// Enable: tick the component, or start the player with <c>-mine-haul</c> or env <c>MINE_HAUL=1</c>.
    /// Brain URL: <see cref="baseUrl"/>, or <c>-brain-api http://host:8080</c>, or env <c>BRAIN_API</c>.
    /// Transport: plain HTTP/1.1 over a TcpClient on a worker thread (same IL2CPP-proven API as BrainClient, and not subject to
    /// Unity's "insecure HTTP not allowed" player setting). <see cref="useUnityWebRequest"/> switches to UnityWebRequest when the
    /// UnityWebRequest module is present (define MINESIM_UWR, set by the asmdef) - that path needs
    /// Player Settings > Allow downloads over HTTP = Always allowed for http:// URLs.
    /// </summary>
    public class MineGoalCycler : MonoBehaviour
    {
        [Header("Brain web API")]
        public string baseUrl = "http://127.0.0.1:8080";
        [Tooltip("Use UnityWebRequest instead of the built-in socket client (needs the UnityWebRequest module and HTTP allowed in Player Settings)")]
        public bool useUnityWebRequest = false;
        [Tooltip("Seconds before an HTTP request is abandoned")]
        public float requestTimeout = 3f;
        [Tooltip("Re-send the current goal this often (s) so a restarted brain picks it up again")]
        public float repostSeconds = 5f;
        [Tooltip("DELETE every truck's goal when this component is disabled")]
        public bool clearGoalsOnDisable = true;

        [Header("Cycle")]
        [Tooltip("Site type trucks load at (round-robin over sites of this type, sorted by id)")]
        public string loadSiteType = "ore";
        [Tooltip("Site type trucks dump at (first of this type)")]
        public string dumpSiteType = "crusher";
        [Tooltip("Optional explicit truck ids to drive (empty = every TruckAgent in the scene)")]
        public string[] truckIds = new string[0];
        [Tooltip("Policy arrive radius (spec: brain/brain/policy.py ARRIVE_RADIUS = 4 m)")]
        public float arriveRadius = 4f;
        [Tooltip("Extra metres added to the arrive radius when checking that a leg's final goal was reached")]
        public float arriveSlack = 1f;
        [Tooltip("Intermediate waypoints advance at this distance (must be > arriveRadius so the policy never reports 'arrived' mid-route)")]
        public float passRadius = 9f;
        [Tooltip("Seconds parked at the load / dump point")]
        public float dwellSeconds = 6f;
        [Tooltip("Stagger the trucks' start by this many seconds each")]
        public float startStagger = 4f;

        [Header("Route")]
        [Tooltip("Drive the road graph instead of a straight line (the policy itself steers straight at its goal)")]
        public bool followRoads = true;
        [Tooltip("Spacing (m) of the goals posted along a road route")]
        public float waypointSpacing = 20f;
        [Tooltip("Skip the current waypoint after this many seconds without getting 1 m closer (0 = never)")]
        public float stuckSeconds = 25f;

        [Header("Debug")]
        public bool logGoals = true;
        public bool drawGizmos = true;

        public enum Phase { Waiting, ToLoad, Loading, ToDump, Dumping }

        [Serializable]
        public class TruckState
        {
            public string id;
            public Transform transform;
            public Phase phase;
            public string loadSite;
            public int waypoint;
            public List<Vector3> route = new List<Vector3>();
            public float timer;
            public float bestDist = float.PositiveInfinity;
            public float bestTime;
            public bool posted;
            public float lastPost = -999f;
            public bool inFlight;
            public string lastError;
            public int cycles;
        }

        [SerializeField] List<TruckState> trucks = new List<TruckState>();
        public IReadOnlyList<TruckState> Trucks => trucks;

        MineGoals goals;
        MineRoadGraph graph;
        float nextScan;

        // ------------------------------------------------------------------ http (socket worker)
        struct Req { public string method, path, body, truck; }
        struct Res { public string truck; public int status; public string error; }

        readonly BlockingCollection<Req> outbox = new BlockingCollection<Req>(256);
        readonly ConcurrentQueue<Res> inbox = new ConcurrentQueue<Res>();
        Thread worker;
        volatile bool running;
        string host = "127.0.0.1";
        int port = 8080;
        string pathPrefix = "";

        void Awake()
        {
            string arg = Arg("-brain-api") ?? Environment.GetEnvironmentVariable("BRAIN_API");
            if (!string.IsNullOrEmpty(arg)) baseUrl = arg;
            if (HasArg("-mine-haul") || Environment.GetEnvironmentVariable("MINE_HAUL") == "1") enabled = true;
        }

        void OnEnable()
        {
            ParseUrl();
            goals = MineGoals.Instance;
            var world = FindFirstObjectByType<MineWorldRoot>();
            graph = world != null ? world.graph : FindFirstObjectByType<MineRoadGraph>();
            trucks.Clear();
            nextScan = 0f;
            running = true;
            if (!useUnityWebRequest || !UwrAvailable)
            {
                worker = new Thread(Worker) { IsBackground = true, Name = "MineGoalCycler.http" };
                worker.Start();
            }
            Debug.Log($"[MineGoalCycler] haul cycle ON -> {baseUrl} ({loadSiteType} load_point -> {dumpSiteType} dump_point)");
        }

        void OnDisable()
        {
            if (clearGoalsOnDisable && running)
                foreach (var t in trucks) Send("DELETE", GoalPath(t.id), null, t.id);
            running = false;
            // the worker drains what is queued (DELETEs) and exits; it is a background thread, so quitting never blocks
            worker?.Join(clearGoalsOnDisable ? 1500 : 100);
            worker = null;
        }

        static bool UwrAvailable
        {
            get
            {
#if MINESIM_UWR
                return true;
#else
                return false;
#endif
            }
        }

        void ParseUrl()
        {
            try
            {
                var u = new Uri(baseUrl.Contains("://") ? baseUrl : "http://" + baseUrl);
                host = u.Host;
                port = u.Port > 0 ? u.Port : 80;
                pathPrefix = u.AbsolutePath.TrimEnd('/');
            }
            catch (Exception e)
            {
                Debug.LogWarning($"[MineGoalCycler] bad baseUrl '{baseUrl}': {e.Message}; using 127.0.0.1:8080");
                host = "127.0.0.1";
                port = 8080;
                pathPrefix = "";
            }
        }

        static string GoalPath(string id) => "/api/trucks/" + Uri.EscapeDataString(id) + "/goal";

        // ------------------------------------------------------------------ cycle
        void Update()
        {
            while (inbox.TryDequeue(out var r)) OnResult(r);

            if (Time.time >= nextScan)
            {
                nextScan = Time.time + 2f;
                ScanTrucks();
            }
            if (goals == null) goals = MineGoals.Instance;
            if (goals == null) return;

            float now = Time.time;
            for (int i = 0; i < trucks.Count; i++)
            {
                var t = trucks[i];
                if (t.transform == null) continue;
                Step(t, now);
            }
        }

        void ScanTrucks()
        {
            foreach (var mb in FindObjectsByType<MonoBehaviour>(FindObjectsSortMode.InstanceID))
            {
                if (mb == null || mb.GetType().Name != "TruckAgent") continue;
                string id = ReadField(mb, "truckId");
                if (string.IsNullOrEmpty(id)) continue;
                if (truckIds != null && truckIds.Length > 0 && Array.IndexOf(truckIds, id) < 0) continue;
                bool known = false;
                foreach (var t in trucks) if (t.id == id) { known = true; t.transform = mb.transform; }
                if (known) continue;
                var st = new TruckState { id = id, transform = mb.transform, phase = Phase.Waiting, timer = Time.time + startStagger * trucks.Count };
                trucks.Add(st);
            }
        }

        void Step(TruckState t, float now)
        {
            switch (t.phase)
            {
                case Phase.Waiting:
                    if (now >= t.timer) BeginLeg(t, Phase.ToLoad, now);
                    return;
                case Phase.Loading:
                    if (now >= t.timer) BeginLeg(t, Phase.ToDump, now);
                    return;
                case Phase.Dumping:
                    if (now >= t.timer) { t.cycles++; BeginLeg(t, Phase.ToLoad, now); }
                    return;
            }

            if (t.route.Count == 0) { BeginLeg(t, t.phase, now); return; }
            Vector3 p = t.transform.position;
            Vector3 g = t.route[t.waypoint];
            float d = Flat(p, g);
            bool last = t.waypoint >= t.route.Count - 1;

            if (d < t.bestDist - 1f) { t.bestDist = d; t.bestTime = now; }
            bool stuck = stuckSeconds > 0f && now - t.bestTime > stuckSeconds;

            if (!last && (d < passRadius || stuck))
            {
                if (stuck && logGoals) Debug.Log($"[MineGoalCycler] {t.id} no progress for {stuckSeconds:0}s, skipping waypoint {t.waypoint}");
                t.waypoint++;
                Post(t, now, true);
                return;
            }
            if (last && d < arriveRadius + arriveSlack)
            {
                bool loading = t.phase == Phase.ToLoad;
                t.phase = loading ? Phase.Loading : Phase.Dumping;
                t.timer = now + dwellSeconds;
                if (logGoals) Debug.Log($"[MineGoalCycler] {t.id} arrived at {(loading ? "load point (" + t.loadSite + ")" : "dump point")}, dwelling {dwellSeconds:0}s");
                return;
            }
            if (last && stuck)
            {
                // can't reach the final point: re-plan the leg from where the truck is
                if (logGoals) Debug.Log($"[MineGoalCycler] {t.id} stuck near the end of the leg, re-planning");
                BeginLeg(t, t.phase, now);
                return;
            }
            if (!t.posted || now - t.lastPost > repostSeconds) Post(t, now, false);
        }

        void BeginLeg(TruckState t, Phase phase, float now)
        {
            t.phase = phase;
            MineGoals.SiteGoal site;
            Vector3 end;
            if (phase == Phase.ToLoad)
            {
                site = PickLoadSite(t);
                if (site == null) { Warn(t, $"no '{loadSiteType}' site in MineGoals"); t.phase = Phase.Waiting; t.timer = now + 5f; return; }
                t.loadSite = site.siteId;
                end = site.hasLoadPoint ? site.loadPoint : site.position;
            }
            else
            {
                site = goals.FirstOfType(dumpSiteType);
                if (site == null) { Warn(t, $"no '{dumpSiteType}' site in MineGoals"); t.phase = Phase.Waiting; t.timer = now + 5f; return; }
                end = site.hasDumpPoint ? site.dumpPoint : site.position;
            }

            t.route.Clear();
            if (followRoads && graph != null && graph.HasNode(site.siteId))
            {
                var r = graph.GetRouteFrom(t.transform.position, site.siteId);
                Resample(r, waypointSpacing, t.route);
                // drop leading points the truck is already past/at
                while (t.route.Count > 1 && Flat(t.route[0], t.transform.position) < passRadius) t.route.RemoveAt(0);
                // the site node is the site centre; finish at the load/dump point instead
                if (t.route.Count > 0 && Flat(t.route[t.route.Count - 1], end) < waypointSpacing * 0.5f) t.route.RemoveAt(t.route.Count - 1);
            }
            t.route.Add(end);
            t.waypoint = 0;
            t.bestDist = float.PositiveInfinity;
            t.bestTime = now;
            if (logGoals) Debug.Log($"[MineGoalCycler] {t.id} -> {(phase == Phase.ToLoad ? "load " : "dump ")}{site.siteId} ({t.route.Count} goals, end {end.x:0.0},{end.z:0.0})");
            Post(t, now, true);
        }

        MineGoals.SiteGoal PickLoadSite(TruckState t)
        {
            var list = goals.OfType(loadSiteType);
            if (list.Count == 0) return null;
            int idx = trucks.IndexOf(t);
            return list[(Mathf.Max(0, idx) + t.cycles) % list.Count];
        }

        static void Resample(List<Vector3> pts, float spacing, List<Vector3> outList)
        {
            if (pts == null || pts.Count == 0) return;
            spacing = Mathf.Max(2f, spacing);
            outList.Add(pts[0]);
            float acc = 0f;
            for (int i = 1; i < pts.Count; i++)
            {
                acc += Flat(pts[i - 1], pts[i]);
                if (acc >= spacing || i == pts.Count - 1)
                {
                    outList.Add(pts[i]);
                    acc = 0f;
                }
            }
        }

        static float Flat(Vector3 a, Vector3 b)
        {
            float dx = a.x - b.x, dz = a.z - b.z;
            return Mathf.Sqrt(dx * dx + dz * dz);
        }

        void Post(TruckState t, float now, bool force)
        {
            if (t.waypoint < 0 || t.waypoint >= t.route.Count) return;
            if (t.inFlight && !force) return;
            t.posted = true;
            t.lastPost = now;
            Send("POST", GoalPath(t.id), MineGoals.GoalJson(t.route[t.waypoint]), t.id);
            t.inFlight = true;
        }

        void OnResult(Res r)
        {
            foreach (var t in trucks)
            {
                if (t.id != r.truck) continue;
                t.inFlight = false;
                bool ok = r.status >= 200 && r.status < 300;
                string err = ok ? null : (r.error ?? ("HTTP " + r.status));
                if (!ok)
                {
                    t.posted = false;   // retry on the next repost tick
                    t.lastPost = Time.time;
                    if (err != t.lastError)
                        Debug.LogWarning($"[MineGoalCycler] goal for {t.id} failed: {err}" +
                                         (r.status == 404 ? " (brain doesn't know this truck yet - is the sim connected?)" : ""));
                }
                t.lastError = err;
            }
        }

        void Warn(TruckState t, string msg)
        {
            if (t.lastError == msg) return;
            t.lastError = msg;
            Debug.LogWarning("[MineGoalCycler] " + t.id + ": " + msg);
        }

        void Send(string method, string path, string body, string truck)
        {
            var req = new Req { method = method, path = pathPrefix + path, body = body, truck = truck };
#if MINESIM_UWR
            if (useUnityWebRequest)
            {
                if (isActiveAndEnabled) StartCoroutine(SendUwr(req));
                return;
            }
#endif
            if (!outbox.TryAdd(req)) inbox.Enqueue(new Res { truck = truck, status = 0, error = "request queue full" });
        }

#if MINESIM_UWR
        IEnumerator SendUwr(Req req)
        {
            string url = "http://" + host + ":" + port.ToString(CultureInfo.InvariantCulture) + req.path;
            using (var w = new UnityWebRequest(url, req.method))
            {
                if (req.body != null)
                {
                    w.uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes(req.body));
                    w.SetRequestHeader("Content-Type", "application/json");
                }
                w.downloadHandler = new DownloadHandlerBuffer();
                w.timeout = Mathf.Max(1, Mathf.CeilToInt(requestTimeout));
                yield return w.SendWebRequest();
                bool netError = w.result == UnityWebRequest.Result.ConnectionError || w.result == UnityWebRequest.Result.DataProcessingError;
                inbox.Enqueue(new Res { truck = req.truck, status = netError ? 0 : (int)w.responseCode, error = netError ? w.error : null });
            }
        }
#endif

        void Worker()
        {
            while (true)
            {
                Req req;
                try
                {
                    if (!outbox.TryTake(out req, 100))
                    {
                        if (!running) return;
                        continue;
                    }
                }
                catch (Exception) { return; }
                inbox.Enqueue(HttpOnce(req));
            }
        }

        Res HttpOnce(Req req)
        {
            var res = new Res { truck = req.truck };
            try
            {
                int ms = Mathf.Max(500, (int)(requestTimeout * 1000f));
                using (var client = new TcpClient())
                {
                    var ar = client.BeginConnect(host, port, null, null);
                    if (!ar.AsyncWaitHandle.WaitOne(ms)) throw new TimeoutException("connect timeout");
                    client.EndConnect(ar);
                    client.NoDelay = true;
                    client.ReceiveTimeout = ms;
                    client.SendTimeout = ms;
                    var stream = client.GetStream();
                    byte[] body = req.body != null ? Encoding.UTF8.GetBytes(req.body) : new byte[0];
                    var sb = new StringBuilder();
                    sb.Append(req.method).Append(' ').Append(req.path).Append(" HTTP/1.1\r\n");
                    sb.Append("Host: ").Append(host).Append(':').Append(port.ToString(CultureInfo.InvariantCulture)).Append("\r\n");
                    sb.Append("User-Agent: MineGoalCycler\r\nAccept: application/json\r\nConnection: close\r\n");
                    if (req.body != null) sb.Append("Content-Type: application/json\r\n");
                    sb.Append("Content-Length: ").Append(body.Length.ToString(CultureInfo.InvariantCulture)).Append("\r\n\r\n");
                    byte[] head = Encoding.ASCII.GetBytes(sb.ToString());
                    stream.Write(head, 0, head.Length);
                    if (body.Length > 0) stream.Write(body, 0, body.Length);

                    // status line is enough: "HTTP/1.1 200 OK"
                    var buf = new byte[512];
                    int n = 0;
                    while (n < buf.Length)
                    {
                        int r = stream.Read(buf, n, buf.Length - n);
                        if (r <= 0) break;
                        n += r;
                        if (Array.IndexOf(buf, (byte)'\n', 0, n) >= 0) break;
                    }
                    string line = Encoding.ASCII.GetString(buf, 0, n);
                    int sp = line.IndexOf(' ');
                    if (sp < 0 || line.Length < sp + 4 || !int.TryParse(line.Substring(sp + 1, 3), NumberStyles.Integer, CultureInfo.InvariantCulture, out res.status))
                        res.error = "bad HTTP response";
                }
            }
            catch (Exception e)
            {
                res.status = 0;
                res.error = e.GetType().Name + ": " + e.Message;
            }
            return res;
        }

        // ------------------------------------------------------------------ helpers
        static string ReadField(MonoBehaviour mb, string field)
        {
            try { return MiniJson.Str(MiniJson.Parse(JsonUtility.ToJson(mb)), field, null); }
            catch (Exception) { return null; }
        }

        static string Arg(string name)
        {
            var args = Environment.GetCommandLineArgs();
            for (int i = 0; i < args.Length - 1; i++) if (args[i] == name) return args[i + 1];
            return null;
        }

        static bool HasArg(string name)
        {
            foreach (var a in Environment.GetCommandLineArgs()) if (a == name) return true;
            return false;
        }

        void OnDrawGizmos()
        {
            if (!drawGizmos) return;
            foreach (var t in trucks)
            {
                if (t.route == null || t.route.Count == 0) continue;
                Gizmos.color = t.phase == Phase.ToLoad ? new Color(1f, 0.8f, 0.2f) : new Color(0.3f, 0.8f, 1f);
                for (int i = Mathf.Max(0, t.waypoint); i < t.route.Count; i++)
                {
                    Gizmos.DrawWireSphere(t.route[i] + Vector3.up, i == t.waypoint ? 2f : 0.8f);
                    if (i > t.waypoint) Gizmos.DrawLine(t.route[i - 1] + Vector3.up, t.route[i] + Vector3.up);
                }
            }
        }
    }
}
