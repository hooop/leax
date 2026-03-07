#include <stdlib.h>
#include <string.h>


char	*create(void)
{
	char	*buf;

	buf = malloc(64);
	strcpy(buf, "hello");
	return (buf);
}

void	process(char *ptr)
{
	ptr[0] = 'H';
}

int	main(void)
{
	char	*data;

	data = create();
	process(data);
	return (0);
}
