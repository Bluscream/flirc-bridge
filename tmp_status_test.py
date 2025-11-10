from flirc_bridge.application import BridgeRuntime
from flirc_bridge.web import create_app
from fastapi.testclient import TestClient

runtime = BridgeRuntime()
runtime.start()
app = create_app(runtime=runtime)
client = TestClient(app)
response = client.get('/api/status')
print('status:', response.status_code)
print('body:', response.text)
runtime.stop()
