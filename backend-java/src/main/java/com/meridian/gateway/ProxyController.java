package com.meridian.gateway;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;

/**
 * Read-only pass-through proxy for the three Meridian read endpoints. The body
 * is forwarded byte-for-byte (no deserialize/reserialize) and the upstream
 * status code is propagated unchanged, so a gateway response is identical to
 * hitting the FastAPI backend directly.
 */
@RestController
public class ProxyController {

    private final WebClient client;

    public ProxyController(WebClient.Builder builder, @Value("${meridian.upstream}") String upstream) {
        this.client = builder.baseUrl(upstream).build();
    }

    @GetMapping("/incidents")
    public Mono<ResponseEntity<String>> incidents() {
        return proxy("/incidents");
    }

    @GetMapping("/incidents/{id}")
    public Mono<ResponseEntity<String>> incident(@PathVariable String id) {
        return proxy("/incidents/" + id);
    }

    @GetMapping("/eval/latest")
    public Mono<ResponseEntity<String>> evalLatest() {
        return proxy("/eval/latest");
    }

    private Mono<ResponseEntity<String>> proxy(String path) {
        return client.get()
                .uri(path)
                .exchangeToMono(response -> response.bodyToMono(String.class)
                        .defaultIfEmpty("")
                        .map(body -> ResponseEntity
                                .status(response.statusCode())
                                .contentType(response.headers().contentType()
                                        .orElse(MediaType.APPLICATION_JSON))
                                .body(body)));
    }
}
