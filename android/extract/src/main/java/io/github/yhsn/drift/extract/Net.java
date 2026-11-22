package io.github.yhsn.drift.extract;

import java.io.IOException;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;

import okhttp3.OkHttpClient;
import okhttp3.RequestBody;
import okhttp3.ResponseBody;

import org.schabi.newpipe.extractor.downloader.Downloader;
import org.schabi.newpipe.extractor.downloader.Request;
import org.schabi.newpipe.extractor.downloader.Response;
import org.schabi.newpipe.extractor.exceptions.ReCaptchaException;

/** NewPipeExtractor's HTTP: one OkHttp client, a desktop browser's user agent (what YouTube's web pages expect). */
public final class Net extends Downloader {
    public static final String UA =
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0";

    public final OkHttpClient http;

    public Net() {
        this(new OkHttpClient.Builder()
                .connectTimeout(15, TimeUnit.SECONDS)
                .readTimeout(30, TimeUnit.SECONDS)
                .build());
    }

    public Net(OkHttpClient http) {
        this.http = http;
    }

    @Override
    public Response execute(Request request) throws IOException, ReCaptchaException {
        byte[] data = request.dataToSend();
        RequestBody body = data == null ? null : RequestBody.create(data);
        okhttp3.Request.Builder b = new okhttp3.Request.Builder()
                .method(request.httpMethod(), body)
                .url(request.url())
                .header("User-Agent", UA);
        for (Map.Entry<String, List<String>> h : request.headers().entrySet()) {
            b.removeHeader(h.getKey());
            for (String v : h.getValue()) b.addHeader(h.getKey(), v);
        }
        try (okhttp3.Response r = http.newCall(b.build()).execute()) {
            if (r.code() == 429) throw new ReCaptchaException("reCaptcha challenge requested", request.url());
            ResponseBody rb = r.body();
            return new Response(r.code(), r.message(), r.headers().toMultimap(),
                    rb == null ? null : rb.string(), r.request().url().toString());
        }
    }
}
