// src/index.ts
import { Buffer } from 'node:buffer'; // Импортируем Buffer для работы с base64

// Интерфейс для входящих данных от бота
interface BotRequestData {
    content: Array<string | { mime_type: string; data_base64: string }>;
    // Опционально: можно передавать и другие параметры, если нужно
    // model_name?: string;
    // generation_config?: object;
}

// Интерфейс для переменных окружения (секретов)
export interface Env {
    PROXY_AUTH_TOKEN: string;
    GEMINI_API_KEY: string; // Добавили ключ Google API
}

// Интерфейс (упрощенный) для частей контента, отправляемых в Google API
interface GoogleApiPart {
    text?: string;
    inlineData?: {
        mimeType: string;
        data: string; // Base64 строка БЕЗ префикса data:mime/type;base64,
    };
}

// Интерфейс (упрощенный) для ответа от Google API
interface GoogleApiResponse {
    candidates?: Array<{
        content?: {
            parts?: Array<{ text?: string }>;
            role?: string;
        };
        finishReason?: string; // Используем строковое представление
        // ... другие поля safetyRatings и т.д.
    }>;
    promptFeedback?: {
        blockReason?: string;
        // ... safetyRatings
    };
    // Могут быть и другие поля ошибки
    error?: {
        code: number;
        message: string;
        status: string;
    };
}


export default {
    async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
        const url = new URL(request.url);
        if (request.method !== 'POST' || url.pathname !== '/generate') {
            return new Response('Not Found', { status: 404 });
        }

        // 1. Проверка токена авторизации
        const authToken = request.headers.get('X-Auth-Token');
        if (!authToken || authToken !== env.PROXY_AUTH_TOKEN) {
            console.error("Unauthorized access attempt");
            return new Response('Unauthorized', { status: 401 });
        }

        // 2. Получение данных от бота
        let botRequestData: BotRequestData;
        try {
            botRequestData = await request.json();
            if (!botRequestData || !Array.isArray(botRequestData.content)) {
                throw new Error('Invalid request body format');
            }
        } catch (e: any) {
            console.error("Failed to parse request body:", e);
            return new Response(`Bad Request: ${e.message || 'Invalid JSON'}`, { status: 400 });
        }

        // 3. Подготовка контента для Google Gemini API
        const googleApiContents: Array<{ parts: GoogleApiPart[] }> = [{ parts: [] }];
        try {
            for (const part of botRequestData.content) {
                if (typeof part === 'string') {
                    googleApiContents[0].parts.push({ text: part });
                } else if (typeof part === 'object' && part.mime_type && part.data_base64) {
                    // Удаляем префикс 'data:mime/type;base64,' если он есть
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
                     console.warn("Skipping invalid content part:", part);
                }
            }
        } catch (e: any) {
             console.error("Error processing content parts:", e);
             return new Response(`Error processing content: ${e.message}`, { status: 400 });
        }


        // Проверяем, что есть что отправлять
         if (googleApiContents[0].parts.length === 0) {
             console.error("No valid content parts to send to Gemini.");
             return new Response('Bad Request: No valid content parts provided', { status: 400 });
         }


        // 4. Вызов Google Gemini API
        const modelName = 'gemini-1.5-flash-latest'; // Можно сделать настраиваемым
        const googleApiUrl = `https://generativelanguage.googleapis.com/v1beta/models/${modelName}:generateContent?key=${env.GEMINI_API_KEY}`;

        console.log(`Sending request to Gemini API: ${googleApiUrl.split('?')[0]}...`); // Не логируем ключ

        try {
            const googleResponse = await fetch(googleApiUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    contents: googleApiContents,
                    // Можно добавить generationConfig, safetySettings, если нужно
                }),
            });

            // 5. Обработка ответа Google
            const googleResponseData: GoogleApiResponse = await googleResponse.json();

            if (!googleResponse.ok) {
                // Если статус не 2xx, логируем ошибку от Google и возвращаем ее
                const errorDetails = googleResponseData.error ?
                    `${googleResponseData.error.status} (${googleResponseData.error.code}): ${googleResponseData.error.message}`
                    : `Status ${googleResponse.status}`;
                console.error(`Gemini API Error: ${errorDetails}`, JSON.stringify(googleResponseData));
                return new Response(`Gemini API Error: ${errorDetails}`, { status: googleResponse.status });
            }

            // Проверяем блокировку в ответе
            if (googleResponseData.promptFeedback?.blockReason) {
                 console.warn(`Gemini request blocked. Reason: ${googleResponseData.promptFeedback.blockReason}`);
                 return new Response(JSON.stringify({ error: `Request blocked by safety settings: ${googleResponseData.promptFeedback.blockReason}`}), {
                     headers: { 'Content-Type': 'application/json' },
                     status: 400 // Или другой подходящий статус
                 });
            }

            // Извлекаем текст из первого кандидата
            const generatedText = googleResponseData.candidates?.[0]?.content?.parts?.[0]?.text;

            if (generatedText) {
                console.log("Successfully received response from Gemini.");
                // Возвращаем успешный ответ с сгенерированным текстом
                return new Response(JSON.stringify({ response: generatedText }), {
                    headers: { 'Content-Type': 'application/json' },
                    status: 200,
                });
            } else {
                // Если текст не найден, но ошибки не было (странно, но возможно)
                console.error("Gemini response missing generated text.", JSON.stringify(googleResponseData));
                return new Response(JSON.stringify({ error: "Gemini response structure invalid or missing text" }), {
                     headers: { 'Content-Type': 'application/json' },
                     status: 500
                });
            }

        } catch (e: any) {
            console.error("Error calling Gemini API:", e);
            // Ошибка сети или при обработке ответа
            return new Response(`Failed to call Gemini API: ${e.message}`, { status: 502 }); // 502 Bad Gateway
        }
    },
};