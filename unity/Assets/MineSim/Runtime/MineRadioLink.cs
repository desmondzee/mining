using System;
using System.Collections.Generic;
using System.IO;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using UnityEngine;

namespace MineSim
{
    /// <summary>
    /// Read-only link to the radio service: polls <c>GET {radioUrl}/api/radio/excavators</c> every
    /// <see cref="pollInterval"/> s on a background thread (plain HTTP/1.1 over TcpClient, no UnityWebRequest, so
    /// Unity's "insecure HTTP" player setting does not matter) and sets <see cref="MineExcavatorRig.digging"/> by id on
    /// the main thread. It never touches trucks, TruckAgent, BrainClient or goals.
    /// URL: command line <c>-radio-url http://host:8090</c> &gt; env <c>RADIO_URL</c> &gt; inspector <see cref="radioUrl"/>.
    /// If the radio is unreachable it logs once, keeps the last state and retries every <see cref="retryInterval"/> s.
    /// </summary>
    [DisallowMultipleComponent]
    public class MineRadioLink : MonoBehaviour
    {
        public string radioUrl = "http://127.0.0.1:8090";
        public string path = "/api/radio/excavators";
        public float pollInterval = 0.5f;
        public float retryInterval = 2f;
        public int timeoutMs = 1500;
        [Tooltip("Log every state change received from the radio")]
        public bool logChanges = true;

        [Serializable]
        public class ExcavatorState
        {
            public string id;
            public string face;
            public string state;
            public float heading_deg;
            public float[] pos3;
        }

        [Serializable]
        class Wrapper
        {
            public ExcavatorState[] items;
            public ExcavatorState[] excavators;
        }

        /// <summary>True after the last poll succeeded.</summary>
        public bool Connected => connected;
        /// <summary>Last states received (main thread).</summary>
        public IReadOnlyList<ExcavatorState> LastStates => lastStates;

        readonly object bodyLock = new object();
        string pendingBody;
        volatile bool connected;
        volatile bool running;
        Thread worker;
        string host;
        int port;
        string basePath;
        readonly List<ExcavatorState> lastStates = new List<ExcavatorState>();
        readonly Dictionary<string, MineExcavatorRig> rigs = new Dictionary<string, MineExcavatorRig>();
        float nextRigScan;
        bool warnedUnknown;

        void Awake()
        {
            string url = Arg("-radio-url") ?? Environment.GetEnvironmentVariable("RADIO_URL");
            if (!string.IsNullOrEmpty(url)) radioUrl = url.Trim();
        }

        void OnEnable()
        {
            if (!ParseUrl(radioUrl, out host, out port, out basePath))
            {
                Debug.LogWarning("[MineRadioLink] unsupported radio URL '" + radioUrl + "' (only http://host[:port] is supported); excavator radio link off");
                return;
            }
            running = true;
            worker = new Thread(Run) { IsBackground = true, Name = "MineRadioLink" };
            worker.Start();
            Debug.Log($"[MineRadioLink] polling http://{host}:{port}{basePath}{path} (read-only, excavator animation only)");
        }

        void OnDisable()
        {
            running = false;
            if (worker != null) worker.Join(timeoutMs + 500);
            worker = null;
            connected = false;
        }

        void Update()
        {
            string body;
            lock (bodyLock)
            {
                body = pendingBody;
                pendingBody = null;
            }
            if (body == null) return;
            var items = Parse(body);
            if (items == null) return;
            lastStates.Clear();
            lastStates.AddRange(items);
            Apply(items);
        }

        // ------------------------------------------------------------------ main thread
        static ExcavatorState[] Parse(string body)
        {
            string s = body.Trim();
            try
            {
                if (s.StartsWith("[")) return JsonUtility.FromJson<Wrapper>("{\"items\":" + s + "}").items ?? new ExcavatorState[0];
                if (s.StartsWith("{"))
                {
                    var w = JsonUtility.FromJson<Wrapper>(s);
                    return w.items ?? w.excavators ?? new ExcavatorState[0];
                }
            }
            catch (Exception e)
            {
                Debug.LogWarning("[MineRadioLink] bad JSON from radio: " + e.Message);
            }
            return null;
        }

        void Apply(ExcavatorState[] items)
        {
            if (Time.unscaledTime >= nextRigScan || rigs.Count == 0) ScanRigs();
            foreach (var it in items)
            {
                if (it == null || string.IsNullOrEmpty(it.id)) continue;
                if (!rigs.TryGetValue(it.id, out var rig) || rig == null)
                {
                    ScanRigs();
                    if (!rigs.TryGetValue(it.id, out rig) || rig == null)
                    {
                        if (!warnedUnknown)
                        {
                            warnedUnknown = true;
                            Debug.LogWarning("[MineRadioLink] no MineExcavatorRig with excavatorId '" + it.id + "' in the scene (logged once)");
                        }
                        continue;
                    }
                }
                bool dig = string.Equals(it.state, "digging", StringComparison.OrdinalIgnoreCase);
                if (rig.digging != dig)
                {
                    rig.digging = dig;
                    if (logChanges) Debug.Log($"[MineRadioLink] {it.id} -> {(dig ? "digging" : "holding")}");
                }
            }
        }

        void ScanRigs()
        {
            nextRigScan = Time.unscaledTime + 5f;
            rigs.Clear();
            foreach (var r in FindObjectsByType<MineExcavatorRig>(FindObjectsInactive.Include, FindObjectsSortMode.None))
                if (r != null && !string.IsNullOrEmpty(r.excavatorId)) rigs[r.excavatorId] = r;
        }

        // ------------------------------------------------------------------ worker thread
        void Run()
        {
            bool warned = false;
            string request = "GET " + basePath + path + " HTTP/1.1\r\n" +
                             "Host: " + host + ":" + port + "\r\n" +
                             "Accept: application/json\r\n" +
                             "User-Agent: MineSim-RadioLink\r\n" +
                             "Connection: close\r\n\r\n";
            byte[] req = Encoding.ASCII.GetBytes(request);
            int pollMs = Mathf.Max(100, Mathf.RoundToInt(pollInterval * 1000f));
            int retryMs = Mathf.Max(200, Mathf.RoundToInt(retryInterval * 1000f));
            while (running)
            {
                int wait;
                try
                {
                    string body = Get(req);
                    lock (bodyLock) pendingBody = body;
                    if (!connected) Debug.Log($"[MineRadioLink] connected to radio at {host}:{port}");
                    connected = true;
                    warned = false;
                    wait = pollMs;
                }
                catch (Exception e)
                {
                    string why = e is AggregateException ae && ae.InnerException != null ? ae.InnerException.Message : e.Message;
                    if (connected || !warned)
                        Debug.LogWarning($"[MineRadioLink] radio unreachable at {host}:{port}{basePath}{path} ({why}); keeping last excavator state, retrying every {retryInterval:0.#} s");
                    warned = true;
                    connected = false;
                    wait = retryMs;
                }
                for (int t = 0; running && t < wait; t += 50) Thread.Sleep(50);
            }
        }

        string Get(byte[] req)
        {
            using (var client = new TcpClient())
            {
                client.NoDelay = true;
                client.ReceiveTimeout = timeoutMs;
                client.SendTimeout = timeoutMs;
                var connect = client.ConnectAsync(host, port);
                connect.ContinueWith(t => { _ = t.Exception; }, System.Threading.Tasks.TaskContinuationOptions.OnlyOnFaulted);
                if (!connect.Wait(timeoutMs) || !client.Connected)
                    throw new IOException("connect timeout");
                using (var stream = client.GetStream())
                {
                    stream.Write(req, 0, req.Length);
                    var ms = new MemoryStream();
                    var buf = new byte[8192];
                    int n;
                    while ((n = stream.Read(buf, 0, buf.Length)) > 0)
                    {
                        ms.Write(buf, 0, n);
                        if (ms.Length > 4 * 1024 * 1024) throw new IOException("response too large");
                    }
                    return ParseResponse(ms.ToArray());
                }
            }
        }

        static string ParseResponse(byte[] data)
        {
            int headerEnd = -1;
            for (int i = 0; i + 3 < data.Length; i++)
                if (data[i] == '\r' && data[i + 1] == '\n' && data[i + 2] == '\r' && data[i + 3] == '\n') { headerEnd = i; break; }
            if (headerEnd < 0) throw new IOException("malformed HTTP response");
            string headers = Encoding.ASCII.GetString(data, 0, headerEnd);
            string[] lines = headers.Split(new[] { "\r\n" }, StringSplitOptions.None);
            string[] status = lines[0].Split(' ');
            if (status.Length < 2 || status[1] != "200") throw new IOException("HTTP " + (status.Length > 1 ? status[1] : "?") + " (is the radio updated with /api/radio/excavators?)");

            bool chunked = false;
            int contentLength = -1;
            for (int i = 1; i < lines.Length; i++)
            {
                int c = lines[i].IndexOf(':');
                if (c <= 0) continue;
                string k = lines[i].Substring(0, c).Trim().ToLowerInvariant();
                string v = lines[i].Substring(c + 1).Trim();
                if (k == "transfer-encoding" && v.ToLowerInvariant().Contains("chunked")) chunked = true;
                else if (k == "content-length") int.TryParse(v, out contentLength);
            }
            int start = headerEnd + 4;
            if (!chunked)
            {
                int len = data.Length - start;
                if (contentLength >= 0 && contentLength < len) len = contentLength;
                return Encoding.UTF8.GetString(data, start, Mathf.Max(0, len));
            }

            var body = new MemoryStream();
            int p = start;
            while (p < data.Length)
            {
                int lineEnd = p;
                while (lineEnd + 1 < data.Length && !(data[lineEnd] == '\r' && data[lineEnd + 1] == '\n')) lineEnd++;
                string sizeStr = Encoding.ASCII.GetString(data, p, lineEnd - p);
                int semi = sizeStr.IndexOf(';');
                if (semi >= 0) sizeStr = sizeStr.Substring(0, semi);
                int size = Convert.ToInt32(sizeStr.Trim(), 16);
                p = lineEnd + 2;
                if (size == 0) break;
                if (p + size > data.Length) throw new IOException("truncated chunk");
                body.Write(data, p, size);
                p += size + 2;
            }
            return Encoding.UTF8.GetString(body.ToArray());
        }

        // ------------------------------------------------------------------ config
        static bool ParseUrl(string url, out string host, out int port, out string basePath)
        {
            host = null;
            port = 80;
            basePath = "";
            if (string.IsNullOrEmpty(url)) return false;
            string u = url.Trim();
            if (u.StartsWith("https://", StringComparison.OrdinalIgnoreCase)) return false;
            if (u.StartsWith("http://", StringComparison.OrdinalIgnoreCase)) u = u.Substring(7);
            int slash = u.IndexOf('/');
            string hostPort = slash >= 0 ? u.Substring(0, slash) : u;
            basePath = slash >= 0 ? u.Substring(slash).TrimEnd('/') : "";
            if (hostPort.StartsWith("["))
            {
                int close = hostPort.IndexOf(']');
                if (close < 0) return false;
                host = hostPort.Substring(1, close - 1);
                string rest = hostPort.Substring(close + 1);
                if (rest.StartsWith(":") && !int.TryParse(rest.Substring(1), out port)) return false;
            }
            else
            {
                int colon = hostPort.LastIndexOf(':');
                if (colon >= 0)
                {
                    host = hostPort.Substring(0, colon);
                    if (!int.TryParse(hostPort.Substring(colon + 1), out port)) return false;
                }
                else host = hostPort;
            }
            return !string.IsNullOrEmpty(host) && port > 0 && port < 65536;
        }

        static string Arg(string name)
        {
            var a = Environment.GetCommandLineArgs();
            for (int i = 0; i < a.Length - 1; i++)
                if (a[i] == name) return a[i + 1];
            return null;
        }
    }
}
