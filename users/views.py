import json
from smtplib import SMTPException

from django.contrib.auth import login as auth_login
from django.contrib.auth import logout
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.views import LoginView
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.views import View
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .forms import CliAuthenticationForm
from .models import User
from .serializers import RegisterSerializer, UserSerializer
from .utils import send_verification_mail


class RegisterView(APIView):
    """Ендпоінт для реєстрації нових користувачів"""

    # Дозволяємо доступ усім (навіть неавторизованим), інакше ніхто не зможе зареєструватися
    permission_classes = (AllowAny,)

    def post(self, request):
        # 1. Передаємо сирі дані з запиту (JSON) у серіалізатор
        serializer = RegisterSerializer(data=request.data)

        # 2. Запускаємо валідацію. Якщо дані невалідні, DRF автоматично
        # зупинить процес і поверне клієнту помилку (наприклад, 400 Bad Request)
        if serializer.is_valid():
            # 3. Викликаємо метод create() серіалізатора, який захешує пароль і створить юзера
            user = serializer.save()

            # 4. Передаємо створеного юзера в інший серіалізатор,
            # щоб безпечно сформувати відповідь БЕЗ пароля
            user_serializer = UserSerializer(user)

            # 5. Повертаємо JSON та статус 201 Created
            return Response(user_serializer.data, status=status.HTTP_201_CREATED)

        # Якщо дані не пройшли перевірку, повертаємо помилки валідації
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class VerifyEmailView(APIView):
    """Ендпоінт для верифікації email за токеном"""

    permission_classes = (AllowAny,)

    def get(self, request, uidb64, token):
        try:
            # Декодуємо ID користувача назад у число
            uid = force_str(urlsafe_base64_decode(uidb64))
            user = User.objects.get(pk=uid)
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            user = None

        # Перевіряємо, чи токен підходить саме цьому користувачу і чи не застарів він
        if user is not None and default_token_generator.check_token(user, token):
            user.is_verified = True
            user.is_active = True  # Робимо акаунт активним
            user.save()
            return Response(
                {"message": "Email успішно підтверджено!"}, status=status.HTTP_200_OK
            )

        return Response(
            {"error": "Посилання недійсне або термін його дії закінчився."},
            status=status.HTTP_400_BAD_REQUEST,
        )


class UserView(APIView):
    """Ендпоінт для перевірки активної сесії клієнта"""

    permission_classes = (IsAuthenticated,)

    def get(self, request):
        if request.user.is_authenticated:
            # Сесія жива! Повертаємо 200 OK та корисні дані для CLI
            return JsonResponse(
                {
                    "status": "authenticated",
                    "username": request.user.username,
                    "email": request.user.email,
                },
                status=200,
            )

            # Сесія застаріла або відсутня
        return JsonResponse(
            {
                "status": "unauthenticated",
                "detail": "Session has expired or is invalid.",
            },
            status=401,
        )


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CliLoginView(LoginView):
    form_class = CliAuthenticationForm

    def get(self, request, *args, **kwargs):
        return JsonResponse(
            {"status": "success", "detail": "CSRF cookie set. Ready for POST."},
            status=200,
        )

    def get_form_kwargs(self):
        """
        Перевизначаємо метод збору даних для форми.
        Якщо дані прийшли як JSON (application/json), ми дістаємо їх з request.body.
        """
        kwargs = super().get_form_kwargs()

        # Перевіряємо, чи клієнт надіслав саме JSON
        if self.request.content_type == "application/json":
            try:
                # Декодуємо тіло запиту
                data = json.loads(self.request.body)

                # Передаємо зчитані дані у форму замість self.request.POST
                kwargs["data"] = data
            except json.JSONDecodeError:
                # Якщо JSON "битий", залишаємо порожній словник
                kwargs["data"] = {}

        return kwargs

    def form_valid(self, form):
        auth_login(self.request, form.get_user())
        return JsonResponse(
            {
                "status": "success",
                "detail": "Успішно авторизовано.",
                "username": form.get_user().username,
            },
            status=200,
        )

    def form_invalid(self, form):
        return JsonResponse(
            {
                "status": "error",
                "detail": "Неправильний логін або пароль.",
                "errors": form.errors.get_json_data(),  # Додасть точні причини (наприклад, порожні поля)
            },
            status=400,
        )


@ensure_csrf_cookie
def get_csrf_token(request):
    return JsonResponse({"detail": "CSRF cookie set"})


class ResendVerificationView(View):
    def post(self, request, *args, **kwargs):
        try:
            data = json.loads(request.body)
            email = data.get("email")
        except json.JSONDecodeError:
            return JsonResponse(
                {"status": "error", "detail": "Некоректний JSON пакет."}, status=400
            )

        if not email:
            return JsonResponse(
                {"status": "error", "detail": "Поле email є обов'язковим."}, status=400
            )

        try:
            user = User.objects.get(email=email)

            # Перевіряємо статус (залежно від вашої логіки: is_verified чи is_active)
            is_verified = user.is_verified

            if is_verified:
                return JsonResponse(
                    {
                        "status": "error",
                        "detail": "Цей акаунт вже верифіковано. Ви можете увійти.",
                    },
                    status=400,
                )

            send_verification_mail(user)

            return JsonResponse(
                {
                    "status": "success",
                    "detail": "Нове посилання для верифікації надіслано на вашу пошту.",
                },
                status=200,
            )

        except User.DoesNotExist:
            # З міркувань безпеки краще повертати успіх, навіть якщо email не знайдено,
            # щоб зловмисники не підбирали існуючі імейли в базі.
            return JsonResponse(
                {
                    "status": "success",
                    "detail": "Якщо цей email зареєстрований, нове посилання надіслано.",
                },
                status=200,
            )

        except SMTPException as e:
            print(e)
            return JsonResponse(
                {"status": "failed", "detail": "Щось пішло не так."}, status=500
            )


class LogoutView(APIView):
    permission_classes = (IsAuthenticated,)  # Тільки для авторизованих

    def post(self, request):
        # Видаляємо токен користувача з бази даних
        logout(request)
        return Response({"detail": "Успішний вихід."}, status=status.HTTP_200_OK)
