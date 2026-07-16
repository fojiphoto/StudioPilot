// GameOS Analytics - minimal Unity SDK for first-party DAU/retention/playtime.
//
// Setup:
//   1. Drop this file in Assets/GameOS/.
//   2. Create an empty GameObject in your first scene, add this component.
//   3. Set `endpoint` to your GameOS collector URL (e.g. https://gameos.yourdomain.com/collect)
//      and `gameKey` to this game's ingest key (from: gameos ingest-key <game_id>).
//
// It sends: install (once per device), session_start (on focus/launch),
// session_end (on pause/quit, with duration). No personal data, no third parties.
//
// Works on Android, iOS and Amazon (FireOS) - no Google dependency.

using System;
using System.Collections;
using System.Text;
using UnityEngine;
using UnityEngine.Networking;

public class GameOSAnalytics : MonoBehaviour
{
    [Header("GameOS collector")]
    public string endpoint = "https://YOUR-GAMEOS-HOST/collect";
    public string gameKey = "PASTE_INGEST_KEY_HERE";
    [Tooltip("2-letter store/platform tag: android | ios | amazon")]
    public string platform = "android";

    private const string InstallFlagKey = "gameos_installed";
    private string _deviceId;
    private float _sessionStart;

    void Awake()
    {
        DontDestroyOnLoad(gameObject);
        _deviceId = SystemInfo.deviceUniqueIdentifier;
    }

    void Start()
    {
        if (PlayerPrefs.GetInt(InstallFlagKey, 0) == 0)
        {
            StartCoroutine(Send("install"));
            PlayerPrefs.SetInt(InstallFlagKey, 1);
            PlayerPrefs.Save();
        }
        BeginSession();
    }

    void BeginSession()
    {
        _sessionStart = Time.realtimeSinceStartup;
        StartCoroutine(Send("session_start"));
    }

    void EndSession()
    {
        float duration = Time.realtimeSinceStartup - _sessionStart;
        StartCoroutine(Send("session_end", duration));
    }

    void OnApplicationPause(bool paused)
    {
        if (paused) EndSession();
        else BeginSession();
    }

    void OnApplicationQuit()
    {
        EndSession();
    }

    IEnumerator Send(string eventType, float durationSec = 0f)
    {
        string country = (System.Globalization.RegionInfo.CurrentRegion != null)
            ? System.Globalization.RegionInfo.CurrentRegion.TwoLetterISORegionName : "";
        string json = "{" +
            "\"game_key\":\"" + Escape(gameKey) + "\"," +
            "\"device_id\":\"" + Escape(_deviceId) + "\"," +
            "\"event_type\":\"" + eventType + "\"," +
            "\"ts\":\"" + DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ") + "\"," +
            "\"duration_sec\":" + durationSec.ToString(System.Globalization.CultureInfo.InvariantCulture) + "," +
            "\"platform\":\"" + Escape(platform) + "\"," +
            "\"country\":\"" + Escape(country) + "\"}";

        using (var req = new UnityWebRequest(endpoint, "POST"))
        {
            req.uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes(json));
            req.downloadHandler = new DownloadHandlerBuffer();
            req.SetRequestHeader("Content-Type", "application/json");
            req.timeout = 15;
            yield return req.SendWebRequest();
            // Fire-and-forget: analytics must never affect gameplay. Failures are ignored
            // (the daily rollup tolerates gaps); consider a local retry queue later.
        }
    }

    static string Escape(string s) => s == null ? "" : s.Replace("\\", "\\\\").Replace("\"", "\\\"");
}
