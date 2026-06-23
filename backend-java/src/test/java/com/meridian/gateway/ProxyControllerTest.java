package com.meridian.gateway;

import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.reactive.server.WebTestClient;

import java.io.IOException;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * The gateway is a transparent read-only proxy: it must pass the upstream body
 * through byte-for-byte and propagate the upstream status code. A MockWebServer
 * stands in for the FastAPI backend so the test needs no live Python process.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class ProxyControllerTest {

    private static MockWebServer upstream;

    @BeforeAll
    static void startUpstream() throws IOException {
        upstream = new MockWebServer();
        upstream.start();
    }

    @AfterAll
    static void stopUpstream() throws IOException {
        upstream.shutdown();
    }

    @DynamicPropertySource
    static void registerUpstream(DynamicPropertyRegistry registry) {
        String base = upstream.url("/").toString().replaceAll("/$", "");
        registry.add("meridian.upstream", () -> base);
    }

    @Autowired
    private WebTestClient client;

    @Test
    void proxiesIncidentsListByteForByte() throws InterruptedException {
        String body = "[{\"id\":\"abc\",\"severity\":\"P0\",\"status\":\"open\"}]";
        upstream.enqueue(new MockResponse()
                .setBody(body)
                .addHeader("Content-Type", "application/json"));

        client.get().uri("/incidents").exchange()
                .expectStatus().isOk()
                .expectBody().json(body);

        RecordedRequest forwarded = upstream.takeRequest();
        assertThat(forwarded.getPath()).isEqualTo("/incidents");
        assertThat(forwarded.getMethod()).isEqualTo("GET");
    }

    @Test
    void proxiesIncidentDetailPath() throws InterruptedException {
        upstream.enqueue(new MockResponse()
                .setBody("{\"id\":\"xyz\",\"root_cause\":\"timeout\"}")
                .addHeader("Content-Type", "application/json"));

        client.get().uri("/incidents/xyz").exchange()
                .expectStatus().isOk()
                .expectBody().jsonPath("$.id").isEqualTo("xyz");

        assertThat(upstream.takeRequest().getPath()).isEqualTo("/incidents/xyz");
    }

    @Test
    void proxiesEvalLatest() throws InterruptedException {
        upstream.enqueue(new MockResponse()
                .setBody("[]")
                .addHeader("Content-Type", "application/json"));

        client.get().uri("/eval/latest").exchange()
                .expectStatus().isOk()
                .expectBody().json("[]");

        assertThat(upstream.takeRequest().getPath()).isEqualTo("/eval/latest");
    }

    @Test
    void propagatesUpstreamErrorStatus() throws InterruptedException {
        upstream.enqueue(new MockResponse()
                .setResponseCode(404)
                .setBody("{\"detail\":\"Not Found\"}")
                .addHeader("Content-Type", "application/json"));

        client.get().uri("/incidents/missing").exchange()
                .expectStatus().isNotFound()
                .expectBody().jsonPath("$.detail").isEqualTo("Not Found");

        // Drain this test's request so the shared MockWebServer queue stays
        // balanced regardless of method execution order (one request per test).
        assertThat(upstream.takeRequest().getPath()).isEqualTo("/incidents/missing");
    }
}
