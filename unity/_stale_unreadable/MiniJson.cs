// Minimal JSON parser/serializer (written for this project, MIT).
// Parse returns: Dictionary<string, object>, List<object>, double, string, bool, or null.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using UnityEngine;

namespace MineSim
{
    public static class MiniJson
    {
        public static object Parse(string json)
        {
            if (string.IsNullOrEmpty(json)) return null;
            var p = new Parser(json);
            try
            {
                p.SkipWs();
                return p.ParseValue();
            }
            catch (Exception e)
            {
                Debug.LogWarning("[MineSim] JSON parse error: " + e.Message);
                return null;
            }
        }

        public static string Escape(string s)
        {
            if (s == null) return "null";
            var sb = new StringBuilder(s.Length + 2);
            sb.Append('"');
            foreach (char c in s)
            {
                switch (c)
                {
                    case '"': sb.Append("\\\""); break;
                    case '\\': sb.Append("\\\\"); break;
                    case '\n': sb.Append("\\n"); break;
                    case '\r': sb.Append("\\r"); break;
                    case '\t': sb.Append("\\t"); break;
                    case '\b': sb.Append("\\b"); break;
                    case '\f': sb.Append("\\f"); break;
                    default:
                        if (c < 0x20) sb.Append("\\u").Append(((int)c).ToString("x4"));
                        else sb.Append(c);
                        break;
                }
            }
            sb.Append('"');
            return sb.ToString();
        }

        // ---- typed accessors (null-safe) ----
        public static Dictionary<string, object> Obj(object o) => o as Dictionary<string, object>;
        public static List<object> Arr(object o) => o as List<object>;

        public static object Get(Dictionary<string, object> d, string key)
        {
            if (d == null) return null;
            return d.TryGetValue(key, out var v) ? v : null;
        }

        public static string Str(Dictionary<string, object> d, string key, string def = null)
        {
            var v = Get(d, key);
            if (v == null) return def;
            return v as string ?? Convert.ToString(v, CultureInfo.InvariantCulture);
        }

        public static float Num(Dictionary<string, object> d, string key, float def = 0f)
        {
            return ToFloat(Get(d, key), def);
        }

        public static float ToFloat(object v, float def = 0f)
        {
            if (v is double dd) return (float)dd;
            if (v is long l) return l;
            if (v is bool b) return b ? 1f : 0f;
            if (v is string s && float.TryParse(s, NumberStyles.Float, CultureInfo.InvariantCulture, out var f)) return f;
            return def;
        }

        /// <summary>[x, z] → Vector2(x, z). Returns false if malformed.</summary>
        public static bool TryVec2(object v, out Vector2 result)
        {
            result = Vector2.zero;
            var a = v as List<object>;
            if (a == null || a.Count < 2) return false;
            result = new Vector2(ToFloat(a[0]), ToFloat(a[1]));
            return true;
        }

        /// <summary>[x, y, z] → Vector3. Returns false if malformed.</summary>
        public static bool TryVec3(object v, out Vector3 result)
        {
            result = Vector3.zero;
            var a = v as List<object>;
            if (a == null || a.Count < 3) return false;
            result = new Vector3(ToFloat(a[0]), ToFloat(a[1]), ToFloat(a[2]));
            return true;
        }

        private sealed class Parser
        {
            private readonly string s;
            private int i;

            public Parser(string json) { s = json; i = 0; }

            public void SkipWs()
            {
                while (i < s.Length && char.IsWhiteSpace(s[i])) i++;
            }

            public object ParseValue()
            {
                SkipWs();
                if (i >= s.Length) throw new FormatException("unexpected end");
                char c = s[i];
                switch (c)
                {
                    case '{': return ParseObject();
                    case '[': return ParseArray();
                    case '"': return ParseString();
                    case 't': Expect("true"); return true;
                    case 'f': Expect("false"); return false;
                    case 'n': Expect("null"); return null;
                    default: return ParseNumber();
                }
            }

            private void Expect(string word)
            {
                if (string.CompareOrdinal(s, i, word, 0, word.Length) != 0)
                    throw new FormatException("expected " + word + " at " + i);
                i += word.Length;
            }

            private Dictionary<string, object> ParseObject()
            {
                var d = new Dictionary<string, object>();
                i++; // {
                SkipWs();
                if (i < s.Length && s[i] == '}') { i++; return d; }
                while (true)
                {
                    SkipWs();
                    if (i >= s.Length || s[i] != '"') throw new FormatException("expected key at " + i);
                    string key = ParseString();
                    SkipWs();
                    if (i >= s.Length || s[i] != ':') throw new FormatException("expected : at " + i);
                    i++;
                    d[key] = ParseValue();
                    SkipWs();
                    if (i >= s.Length) throw new FormatException("unterminated object");
                    if (s[i] == ',') { i++; continue; }
                    if (s[i] == '}') { i++; return d; }
                    throw new FormatException("expected , or } at " + i);
                }
            }

            private List<object> ParseArray()
            {
                var a = new List<object>();
                i++; // [
                SkipWs();
                if (i < s.Length && s[i] == ']') { i++; return a; }
                while (true)
                {
                    a.Add(ParseValue());
                    SkipWs();
                    if (i >= s.Length) throw new FormatException("unterminated array");
                    if (s[i] == ',') { i++; continue; }
                    if (s[i] == ']') { i++; return a; }
                    throw new FormatException("expected , or ] at " + i);
                }
            }

            private string ParseString()
            {
                i++; // opening quote
                var sb = new StringBuilder();
                while (i < s.Length)
                {
                    char c = s[i++];
                    if (c == '"') return sb.ToString();
                    if (c != '\\') { sb.Append(c); continue; }
                    if (i >= s.Length) break;
                    char e = s[i++];
                    switch (e)
                    {
                        case '"': sb.Append('"'); break;
                        case '\\': sb.Append('\\'); break;
                        case '/': sb.Append('/'); break;
                        case 'b': sb.Append('\b'); break;
                        case 'f': sb.Append('\f'); break;
                        case 'n': sb.Append('\n'); break;
                        case 'r': sb.Append('\r'); break;
                        case 't': sb.Append('\t'); break;
                        case 'u':
                            if (i + 4 > s.Length) throw new FormatException("bad \\u escape");
                            sb.Append((char)Convert.ToInt32(s.Substring(i, 4), 16));
                            i += 4;
                            break;
                        default: sb.Append(e); break;
                    }
                }
                throw new FormatException("unterminated string");
            }

            private object ParseNumber()
            {
                int start = i;
                while (i < s.Length && "+-0123456789.eE".IndexOf(s[i]) >= 0) i++;
                if (start == i) throw new FormatException("unexpected char '" + s[i] + "' at " + i);
                string num = s.Substring(start, i - start);
                if (double.TryParse(num, NumberStyles.Float, CultureInfo.InvariantCulture, out var d)) return d;
                throw new FormatException("bad number " + num);
            }
        }
    }
}
