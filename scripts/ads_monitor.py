"""Monitor ADS state on UmRT ports during TcUnit-Runner execution."""
import subprocess
import sys
import time

ADS_DLL = r"C:\Program Files (x86)\Beckhoff\TwinCAT\3.1\Components\Base\v170\TwinCAT.Ads.dll"


def check_ads_state(ams_net_id: str, port: int) -> str:
    ps = (
        f"$ErrorActionPreference='Stop'; "
        f"Add-Type -Path '{ADS_DLL}'; "
        f"$c = New-Object TwinCAT.Ads.TcAdsClient; "
        f"try {{ "
        f"$c.Connect('{ams_net_id}', {port}); "
        f"$s = $c.ReadState(); "
        f"Write-Output ($s.AdsState.ToString() + '/' + $s.DeviceState.ToString()) "
        f"}} catch {{ Write-Output ('ERROR: ' + $_.Exception.InnerException.Message) }} "
        f"finally {{ $c.Dispose() }}"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
    )
    return (result.stdout or "").strip() or (result.stderr or "").strip()[:100]


def main():
    ams = sys.argv[1] if len(sys.argv) > 1 else "199.4.42.250.1.1"
    ports = [300, 851]
    duration = int(sys.argv[2]) if len(sys.argv) > 2 else 360
    interval = 10

    print(f"Monitoring ADS state on {ams} ports {ports} for {duration}s")
    start = time.time()
    while time.time() - start < duration:
        elapsed = int(time.time() - start)
        states = []
        for port in ports:
            state = check_ads_state(ams, port)
            states.append(f"P{port}={state}")
        print(f"[{elapsed:4d}s] {' | '.join(states)}", flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    main()
