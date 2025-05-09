// src/index.ts

// Интерфейс для входящих данных от бота
interface BotRequestData {
    content: Array<string | { mime_type: string; data_base64: string }>;
    // Опционально: можно передавать и другие параметры
    // model_name?: string;
    // temperature?: number; // Например, для управления креативностью
}

// Интерфейс для переменных окружения (секретов), установленных в Cloudflare
export interface Env {
    PROXY_AUTH_TOKEN: string; // Секретный токен для авторизации бота
    GEMINI_API_KEY: string;   // API Ключ для Google Gemini
}

// Интерфейс (упрощенный) для частей контента, отправляемых в Google API
interface GoogleApiPart {
    text?: string;
    inlineData?: {
        mimeType: string;
        data: string; // Строка Base64
    };
}

// Интерфейс (упрощенный) для ответа от Google API
interface GoogleApiResponse {
    candidates?: Array<{
        content?: {
            parts?: Array<{ text?: string }>;
            role?: string;
        };
        finishReason?: string;
        safetyRatings?: Array<any>; // Массив с рейтингами безопасности
    }>;
    promptFeedback?: {
        blockReason?: string;
        safetyRatings?: Array<any>;
    };
    error?: {
        code: number;
        message: string;
        status: string;
    };
}

// --- Функция для повторных попыток Fetch ---
async function fetchWithRetry(url: string, options: RequestInit, maxRetries: number = 3): Promise<Response> {
    let attempt = 0;
    while (attempt < maxRetries) {
        attempt++;
        try {
            const response = await fetch(url, options);
            // Повторяем только при серверных ошибках Google (5xx) или ошибках лимитов (429)
            if ((response.status >= 500 || response.status === 429) && attempt < maxRetries) {
                 // Экспоненциальная задержка (100ms, 200ms, 400ms...) со случайным элементом
                 const waitMs = Math.pow(2, attempt) * 100 + Math.random() * 100;
                 console.warn(`Gemini API request failed with status ${response.status}. Retrying attempt ${attempt}/${maxRetries} after ${waitMs.toFixed(0)}ms...`);
                 await new Promise(resolve => setTimeout(resolve, waitMs));
                 continue; // Переходим к следующей попытке
            }
            // Возвращаем ответ, если он не 5xx/429 или попытки кончились
            return response;
        } catch (error: any) {
             // Повторяем при сетевых ошибках
            if (attempt < maxRetries) {
                const waitMs = Math.pow(2, attempt) * 100 + Math.random() * 100;
                console.warn(`Network error calling Gemini API: ${error.message}. Retrying attempt ${attempt}/${maxRetries} after ${waitMs.toFixed(0)}ms...`);
                await new Promise(resolve => setTimeout(resolve, waitMs));
                continue;
            } else {
                // Если все попытки исчерпаны, пробрасываем ошибку
                console.error(`Failed to call Gemini API after ${maxRetries} attempts: ${error.message}`);
                throw error; // Пробрасываем оригинальную ошибку сети
            }
        }
    }
     // Теоретически недостижимо, но нужно для TS
     throw new Error("fetchWithRetry logic error");
}


export default {
    // Основной обработчик входящих HTTP запросов
    async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
        const url = new URL(request.url);

        // 1. Проверяем метод и путь
        if (request.method !== 'POST' || url.pathname !== '/generate') {
            return new Response('Not Found', { status: 404 });
        }

        // 2. Проверяем токен авторизации
        const authToken = request.headers.get('X-Auth-Token');
        if (!authToken || authToken !== env.PROXY_AUTH_TOKEN) {
            console.error("Unauthorized access attempt denied.");
            return new Response('Unauthorized', { status: 401 });
        }

        // 3. Получаем и валидируем данные от бота
        let botRequestData: BotRequestData;
        try {
            botRequestData = await request.json();
            if (!botRequestData || !Array.isArray(botRequestData.content)) {
                throw new Error('Invalid request body format: "content" array is missing or not an array.');
            }
        } catch (e: any) {
            console.error("Failed to parse request body:", e);
            return new Response(`Bad Request: ${e.message || 'Invalid JSON'}`, { status: 400 });
        }

        // 4. Готовим контент для Google API (конвертируем байты в base64)
        const googleApiContents: Array<{ parts: GoogleApiPart[] }> = [{ parts: [] }];
        try {
            for (const part of botRequestData.content) {
                if (typeof part === 'string') {
                    // Добавляем текстовую часть
                    googleApiContents[0].parts.push({ text: part });
                } else if (typeof part === 'object' && part.mime_type && part.data_base64) {
                    // Добавляем часть с изображением (уже в base64 от бота)
                     // Убедимся, что base64 строка чистая (без префикса data:)
                     const base64Data = part.data_base64.startsWith('data:')
                        ? part.data_base64.substring(part.data_base64.indexOf(',') + 1)
                        : part.data_base64;

                     googleApiContents[0].parts.push({
                        inlineData: {
                            mimeType: part.mime_type,
                            data: base64Data,
                        },
                    });
                } else {
                    console.warn("Skipping invalid content part during preparation:", part);
                }
            }
            if (googleApiContents[0].parts.length === 0) {
                 throw new Error("No valid content parts to send after preparation.");
            }
        } catch (e: any) {
            console.error("Error processing content parts for Google API:", e);
            return new Response(`Bad Request: Error processing content - ${e.message}`, { status: 400 });
        }

        // 5. Вызываем Google Gemini API с помощью fetchWithRetry
        const modelName = 'gemini-2.5-flash-preview-04-17'; 
        const googleApiUrl = `https://generativelanguage.googleapis.com/v1beta/models/${modelName}:generateContent?key=${env.GEMINI_API_KEY}`;
        const requestStartTime = Date.now();
        console.log(`Sending request to Gemini API (${modelName})...`);

        try {
            const googleResponse = await fetchWithRetry(googleApiUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    contents: googleApiContents,
                    // Можно добавить generationConfig и safetySettings при необходимости
                    // generationConfig: { temperature: 0.7 },
                    // safetySettings: [{ category: "HARM_CATEGORY_SEXUALITY", threshold: "BLOCK_LOW_AND_ABOVE" }]
                }),
            });

            const requestDuration = Date.now() - requestStartTime;
            console.log(`Received response from Gemini API. Status: ${googleResponse.status}. Duration: ${requestDuration}ms`);

            // 6. Обрабатываем ответ от Google
            const googleResponseData: GoogleApiResponse = await googleResponse.json();

            // Обработка ошибок HTTP от Google (которые не были 5xx/429 или после retries)
            if (!googleResponse.ok) {
                const errorDetails = googleResponseData.error ? `${googleResponseData.error.status}(${googleResponseData.error.code}): ${googleResponseData.error.message}` : `Status ${googleResponse.status}`;
                console.error(`Gemini API returned HTTP error: ${errorDetails}`, JSON.stringify(googleResponseData));
                // Возвращаем ошибку клиенту (боту)
                return new Response(JSON.stringify({ error: `Gemini API Error: ${errorDetails}` }), {
                    status: googleResponse.status, // Передаем исходный статус ошибки
                    headers: { 'Content-Type': 'application/json' },
                });
            }

            // Проверка блокировки контента
            if (googleResponseData.promptFeedback?.blockReason) {
                 const reason = googleResponseData.promptFeedback.blockReason;
                 console.warn(`Gemini request blocked by safety settings. Reason: ${reason}`);
                 return new Response(JSON.stringify({ error: `Request blocked by safety settings: ${reason}`}), {
                     status: 400, // Bad Request из-за контента
                     headers: { 'Content-Type': 'application/json' },
                 });
            }

            // Извлечение сгенерированного текста
            // Ищем текст в первой части первого кандидата
            const generatedText = googleResponseData.candidates?.[0]?.content?.parts?.[0]?.text;

            if (typeof generatedText === 'string') {
                console.log("Successfully extracted generated text from Gemini response.");
                // Отправляем успешный ответ боту
                return new Response(JSON.stringify({ response: generatedText.trim() }), {
                    status: 200,
                    headers: { 'Content-Type': 'application/json' },
                });
            } else {
                // Если текст не найден, но ошибки не было
                console.error("Gemini response OK, but missing generated text.", JSON.stringify(googleResponseData));
                return new Response(JSON.stringify({ error: "Gemini response structure invalid or missing text part" }), {
                     status: 500, // Внутренняя ошибка сервера (прокси не смог разобрать ответ)
                     headers: { 'Content-Type': 'application/json' },
                });
            }

        } catch (e: any) {
            // Ловим ошибки сети (после retries) или ошибки парсинга JSON ответа Google
            const requestDuration = Date.now() - requestStartTime;
            console.error(`Failed to call or process Gemini API response after ${requestDuration}ms:`, e);
            return new Response(JSON.stringify({ error: `Proxy failed to process request: ${e.message || 'Unknown fetch/parse error'}` }), {
                status: 502, // Bad Gateway - прокси не смог связаться с вышестоящим сервером
                headers: { 'Content-Type': 'application/json' },
            });
        }
    },
};