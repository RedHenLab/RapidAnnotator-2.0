"""Forms for Login/Registeration."""

from flask_wtf import FlaskForm
from flask_login import current_user
from flask_babelex import lazy_gettext as _
from sqlalchemy.orm.exc import NoResultFound
from flask_security.forms import email_required, email_validator, \
    unique_user_email
from wtforms import FormField, PasswordField, StringField, SubmitField, \
    BooleanField
from wtforms.validators import DataRequired, EqualTo, StopValidation, \
    ValidationError, Email

from rapidannotator.models import User
from rapidannotator.validators import USERNAME_RULES, validate_username

''' TODO! min max lengths '''
def strip_filter(text):
    """Filter for trimming whitespace.

    :param text: The text to strip.
    :returns: The stripped text.
    """
    return text.strip() if text else text

class LoginForm(FlaskForm):
    """Form for users to login."""

    username = StringField(
        label=_('Username'),
        validators=[DataRequired(message=_('Username not provided.'))],
        filters=[strip_filter],)

    password = PasswordField(
        label=_('Password'),
        validators=[DataRequired(message=_('Password not provided.'))],)

    remember_me = BooleanField(_('Remember Me'))

class RegistrationForm(FlaskForm):
    username = StringField(
        label=_('Username'),
        description=_('Required. %(username_rules)s',
                      username_rules=USERNAME_RULES),
        validators=[DataRequired(message=_('Username not provided.'))],
        filters=[strip_filter],
    )

    fullname = StringField(
        label=_('Fullname'),
        validators=[DataRequired(message=_('Fullname not provided.'))],
        filters=[strip_filter],
    )

    email = StringField(
        label=_('Email'),
        # validators=[DataRequired(message=_('Email not provided.')), Email()]
    )

    password = PasswordField(
        label=_('Password'),
        validators=[DataRequired(message=_('Password not provided.'))],)

    password2 = PasswordField(
        label=_('Confirm Password'),
        validators=[DataRequired(message=_('Confirmation password not provided.')),
                    EqualTo('password')])

    def validate_username(self, username):
        """Wrap username validator for WTForms."""
        try:
            validate_username(username.data)
        except ValueError as e:
            raise ValidationError(_('Invalid Username'))

        user = User.query.filter_by(username=username.data).first()
        if user is not None:
            raise ValidationError(_('Username already taken!'))

    def validate_email(self, email):
        user = User.query.filter_by(email=email.data).first()
        if user is not None:
            raise ValidationError(_('Email address already registered.'))

class DummyForm(FlaskForm):
    """A Dummy form for testing and debugging."""

    username = StringField(_('Enter something random'),)
    submit = SubmitField(_('Sign In'))

class DummyForm2(FlaskForm):
    """A Dummy form for testing and debugging."""

    username = StringField(_('Enter something random From 2'),)
    email = StringField(_('Email'),)
    password = PasswordField(_('Password'),)
    submit = SubmitField(_('Sign In'))
