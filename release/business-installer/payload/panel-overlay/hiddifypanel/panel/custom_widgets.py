import datetime

from flask_admin.contrib.sqla import ModelView
from flask_babel import lazy_gettext as _
from wtforms import TextAreaField
from wtforms.fields import IntegerField, SelectField, DecimalField
from wtforms.widgets import TextArea

from wtforms import Field
from wtforms.validators import ValidationError
from wtforms.widgets import TextArea
import json

from hiddifypanel.models import *


# from gettext import gettext as _


class DaysLeftField(IntegerField):
    def process_data(self, value):
        if value is not None:
            days_left = (value - datetime.date.today()).days
            self.data = days_left
        else:
            self.data = None

    def process_formdata(self, valuelist):
        if valuelist and valuelist[0]:
            days_left = valuelist[0]
            new_date_value = datetime.date.today() + datetime.timedelta(days=int(days_left))
            self.data = new_date_value
        else:
            self.data = None


class LastResetField(IntegerField):
    def process_data(self, value):
        if value is not None:
            days_left = (datetime.date.today() - value).days
            self.data = days_left
        else:
            self.data = None

    def process_formdata(self, valuelist):
        if valuelist and valuelist[0]:
            days_left = valuelist[0]
            new_date_value = datetime.date.today() - datetime.timedelta(days=int(days_left))
            self.data = new_date_value
        else:
            self.data = None


class CKTextAreaWidget(TextArea):
    extra_js = ['//cdn.ckeditor.com/4.6.0/standard/ckeditor.js']

    def __call__(self, field, **kwargs):
        if kwargs.get('class'):
            kwargs['class'] += ' ckeditor'
        else:
            kwargs.setdefault('class', 'ckeditor')
        return super(CKTextAreaWidget, self).__call__(field, **kwargs)


class CKTextAreaField(TextAreaField):
    extra_js = ['//cdn.ckeditor.com/4.6.0/standard/ckeditor.js']
    widget = CKTextAreaWidget()


class MessageAdmin(ModelView):
    extra_js = ['//cdn.ckeditor.com/4.6.0/standard/ckeditor.js']

    form_overrides = {
        'body': CKTextAreaField
    }


class EnumSelectField(SelectField):
    def __init__(self, enum, *args, **kwargs):
        display_names = {
            "daily": "Ежедневно",
            "weekly": "Еженедельно",
            "monthly": "Ежемесячно",
            "quarterly": "Ежеквартально",
            "semiannual": "Раз в полгода",
            "yearly": "Ежегодно",
            "lifetime": "Пожизненно",
            "no_reset": "Без автосброса",
            "manual": "Вручную",
            "yookassa": "YooKassa",
        }
        choices = [
            (str(enum_value.value), _(display_names.get(enum_value.name, enum_value.name)))
            for enum_value in enum
        ]
        super().__init__(*args, choices=choices, **kwargs)


class UsageField(DecimalField):
    def process_data(self, value):
        if value is not None:
            self.data = value / ONE_GIG
        else:
            self.data = None

    def process_formdata(self, valuelist):

        if valuelist and valuelist[0]:
            self.data = int(float(valuelist[0]) * ONE_GIG)
        else:
            self.data = None







class JSONWidget(TextArea):
    def __call__(self, field, **kwargs):
        if isinstance(field.data, dict):
            try:
                field.data = json.dumps(field.data, indent=2)
            except Exception:
                pass
        if kwargs.get('class'):
            kwargs['class'] += ' ltr json-editor'
        else:
            kwargs.setdefault('class', 'ltr json-editor')

        return super().__call__(field, **kwargs)

class JSONField(Field):
    widget = JSONWidget()

    def _value(self):
        if self.data is None:
            return ''
        if isinstance(self.data, str):
            return self.data
        try:
            return json.dumps(self.data, indent=2)
        except Exception:
            return str(self.data)

    def process_formdata(self, valuelist):
        if valuelist:
            try:
                self.data = json.loads(valuelist[0])
            except Exception as e:
                raise ValidationError(f'Invalid JSON: {e}')
