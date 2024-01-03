K6_SVC_CONTENT = """[Unit]
Description=Service for controlling xk6
Wants=network.target

[Service]
Environment="K6_PROMETHEUS_RW_SERVER_URL=http://prometheus:9090/api/v1/write"
Environment="K6_PROMETHEUS_RW_PUSH_INTERVAL=5s"
ExecStart=/usr/share/xk6_opensearch//k6 run --vus=10 --duration=0 /usr/share/xk6_opensearch//script.js
Restart=always
TimeoutSec=600
Type=simple"""  # noqa

K6_JS_CONTENT = """import opensearch from 'k6/x/opensearch';
import { Trend, Counter } from 'k6/metrics';

const OpenSearchOperation = {
	Create: 0,
	Delete: 1,
	Search: 2,
	Update: 3,
	Index: 4,
};

const xk6_opensearch_success_http_latency = new Trend('xk6_opensearch_success_http_latency', true);
const xk6_opensearch_success_failure_latency = new Trend('xk6_opensearch_success_failure_latency', true);

const xk6_opensearch_total_latency = new Counter('xk6_opensearch_total_latency');
const xk6_opensearch_total_bytes_sent = new Counter('xk6_opensearch_total_bytes_sent');
const xk6_opensearch_total_bytes_received = new Counter('xk6_opensearch_total_bytes_received');


const client = opensearch.open('test', 'test', 'https://localhost:9200');


const indices_list = [
    {
        index: "test1",
        shards: 1,
        replicas: 0
    },
    {
        index: "test2",
        shards: 1,
        replicas: 0
    },
];

export function setup() {
    var l = indices_list.length;
    for (var i = 0; i < l; i++) {
        opensearch.index(client, OpenSearchOperation.Create,  indices_list[i].index, indices_list[i].shards, indices_list[i].replicas);
    }
}



export default function () {
    var res = null;
    var l = indices_list.length;
    for (var i = 0; i < l; i++) {
        res = opensearch.document(client, OpenSearchOperation.Create, 100, indices_list[i].index, '');
        // Seems that k6 js uses ms as standard unit
        if (res.respstatus < 200 || res.respstatus >= 300) {
            xk6_opensearch_success_failure_latency.add(res.latency / 1e6);
        } else {
            xk6_opensearch_success_http_latency.add(res.latency / 1e6);
        }
        xk6_opensearch_total_latency.add(res.latency / 1e6);
        xk6_opensearch_total_bytes_sent.add(res.bytes_sent);
        xk6_opensearch_total_bytes_received.add(res.bytes_received);
    }
}"""  # noqa
