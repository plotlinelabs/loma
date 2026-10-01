// Test helper: forwards like deploy/nginx/conf.d/loma.conf so browser checks see what production sees
// (public Origin from the browser, internal address in request.url). Usage: node nginx-like-proxy.cjs [listen] [upstream]
const http = require('node:http');
const listen = Number(process.argv[2] || 80), upstream = Number(process.argv[3] || 13001);
http.createServer((req, res) => {
  const host = (req.headers.host || '').replace(/:\d+$/, ''); // nginx $host drops the port
  const headers = { ...req.headers, host, 'x-forwarded-host': host, 'x-forwarded-proto': 'http', 'x-forwarded-for': req.socket.remoteAddress };
  const out = http.request({ host: 'localhost', port: upstream, method: req.method, path: req.url, headers }, (r) => {
    res.writeHead(r.statusCode, r.headers); r.pipe(res);
  });
  out.on('error', () => { res.writeHead(502); res.end(); });
  req.pipe(out);
}).listen(listen, '127.0.0.1');
